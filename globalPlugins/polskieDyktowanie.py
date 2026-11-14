# -*- coding: utf-8 -*-
"""Polskie Dyktowanie for NVDA.

Alt+Q starts/stops a text mode in an editable control and opens settings
elsewhere. Alt+Shift+Q runs the selected image, color or video analysis.
"""

# Copyright (C) 2026 Madridista/Realista
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License, version 2 or (at your option) any
# later version. See COPYING.txt for the complete license text.

import base64
import binascii
import ctypes
import io
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
import webbrowser
import zlib
from pathlib import Path
from ctypes import wintypes
import winsound

import api
import controlTypes
import globalPluginHandler
import gui
import keyboardHandler
import ui
import tones
import wx
from scriptHandler import script


ADDON_NAME = "polskieDyktowanie"
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_OPENAI_MODEL = "gpt-6.1-sol"
DEFAULT_OPENAI_VISION_MODEL = "gpt-6.1-terra"
DEFAULT_GROK_VISION_MODEL = "grok-4.7"
DEFAULT_GEMINI_VISION_MODEL = DEFAULT_MODEL
OPENAI_TRANSCRIPTION_MODEL = "gpt-transcribe"
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
MAX_RECORD_SECONDS = 300
MAX_AUDIO_BYTES = 10_000_000
REQUEST_TIMEOUT = 90
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_DEFAULTS = {
    "provider": "gemini",
    "gemini_key_protected": "",
    "xai_key_protected": "",
    "openai_key_protected": "",
    "model": DEFAULT_MODEL,
    "grok_model": "grok-4.7",
    "gemini_vision_model": DEFAULT_GEMINI_VISION_MODEL,
    "grok_vision_model": DEFAULT_GROK_VISION_MODEL,
    "openai_text_model": DEFAULT_OPENAI_MODEL,
    "openai_vision_model": DEFAULT_OPENAI_VISION_MODEL,
    "mode": "dictation",
    "visual_mode": "screen",
    "visual_mode_schema": 1,
    "language": "pl",
    "target_language": "en",
    "smart_correction": True,
    "gender_precision": True,
    "smart_names": True,
    "auto_emoji": False,
    "smart_paragraphs": True,
    "rich_punctuation": True,
    "read_result": False,
    "auto_send": False,
    "separator": "space",
    "dictionary": "",
    "system_dictation": False,
    "profanity_mode": "unchanged",
    "background_voice_protection": "standard",
    "vision_detail": "detailed",
    "video_seconds": 8,
    "tts_provider": "gemini",
    "gemini_tts_model": "gemini-3.1-flash-tts-preview",
    "gemini_tts_voice": "Kore",
    "xai_tts_voice": "eve",
    "openai_tts_model": "gpt-4o-mini-tts",
    "openai_tts_voice": "marin",
    "shortcut_main": "kb:alt+q",
    "shortcut_global": "kb:alt+shift+q",
    "shortcut_speech_to_text": "kb:control+alt+shift+f7",
    "shortcut_text_to_speech": "kb:control+alt+shift+f8",
    "shortcut_file_transcription": "kb:control+alt+shift+f9",
    "shortcut_file_synthesis": "kb:control+alt+shift+f10",
    "shortcut_visual_photo_file": "kb:control+alt+shift+f1",
    "shortcut_visual_photo": "kb:control+alt+shift+f2",
    "shortcut_visual_screen": "kb:control+alt+shift+f3",
    "shortcut_visual_colors": "kb:control+alt+shift+f4",
    "shortcut_visual_video_file": "kb:control+alt+shift+f5",
    "shortcut_visual_video": "kb:control+alt+shift+f6",
    "shortcut_assignment_version": 1,
}


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _dpapi(data, protect):
    """Protect/unprotect UTF-8 data for the current Windows user."""
    raw = bytes(data)
    source_buffer = ctypes.create_string_buffer(raw, len(raw))
    source = _DATA_BLOB(
        len(raw),
        ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    result = _DATA_BLOB()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    if protect:
        fn = crypt32.CryptProtectData
        fn.argtypes = [
            ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR,
            ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
        ]
        fn.restype = wintypes.BOOL
        ok = fn(ctypes.byref(source), "Polskie Dyktowanie", None, None, None, 0, ctypes.byref(result))
    else:
        fn = crypt32.CryptUnprotectData
        fn.argtypes = [
            ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
        ]
        fn.restype = wintypes.BOOL
        description = wintypes.LPWSTR()
        ok = fn(ctypes.byref(source), ctypes.byref(description), None, None, None, 0, ctypes.byref(result))
        if description:
            local_free(ctypes.cast(description, ctypes.c_void_p))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        local_free(ctypes.cast(result.pbData, ctypes.c_void_p))


def _record_wav(stop_event):
    """Record mono 16 kHz PCM using the Windows waveIn API."""
    winmm = ctypes.WinDLL("winmm", use_last_error=True)
    handle = wintypes.HANDLE()
    fmt = _WAVEFORMATEX(
        1, CHANNELS, SAMPLE_RATE,
        SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH,
        CHANNELS * SAMPLE_WIDTH, 16, 0,
    )
    capacity = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH * MAX_RECORD_SECONDS
    audio_buffer = ctypes.create_string_buffer(capacity)
    header = _WAVEHDR(
        ctypes.cast(audio_buffer, ctypes.c_void_p),
        capacity, 0, 0, 0, 0, None, 0,
    )
    prototypes = {
        "waveInOpen": ([ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.POINTER(_WAVEFORMATEX), ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD], wintypes.UINT),
        "waveInPrepareHeader": ([wintypes.HANDLE, ctypes.POINTER(_WAVEHDR), wintypes.UINT], wintypes.UINT),
        "waveInAddBuffer": ([wintypes.HANDLE, ctypes.POINTER(_WAVEHDR), wintypes.UINT], wintypes.UINT),
        "waveInStart": ([wintypes.HANDLE], wintypes.UINT),
        "waveInStop": ([wintypes.HANDLE], wintypes.UINT),
        "waveInUnprepareHeader": ([wintypes.HANDLE, ctypes.POINTER(_WAVEHDR), wintypes.UINT], wintypes.UINT),
        "waveInClose": ([wintypes.HANDLE], wintypes.UINT),
    }
    for name, (argtypes, restype) in prototypes.items():
        fn = getattr(winmm, name)
        fn.argtypes = argtypes
        fn.restype = restype

    prepared = False
    opened = False
    try:
        result = winmm.waveInOpen(ctypes.byref(handle), 0xFFFFFFFF, ctypes.byref(fmt), 0, 0, 0)
        if result:
            raise RuntimeError("Windows nie może otworzyć mikrofonu (waveInOpen: {}).".format(result))
        opened = True
        result = winmm.waveInPrepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
        if result:
            raise RuntimeError("Nie udało się przygotować bufora mikrofonu ({}).".format(result))
        prepared = True
        result = winmm.waveInAddBuffer(handle, ctypes.byref(header), ctypes.sizeof(header))
        if result:
            raise RuntimeError("Nie udało się uruchomić bufora mikrofonu ({}).".format(result))
        result = winmm.waveInStart(handle)
        if result:
            raise RuntimeError("Nie udało się rozpocząć nagrywania ({}).".format(result))
        stop_event.wait(MAX_RECORD_SECONDS)
    finally:
        if opened:
            winmm.waveInStop(handle)
        if prepared:
            winmm.waveInUnprepareHeader(handle, ctypes.byref(header), ctypes.sizeof(header))
        if opened:
            winmm.waveInClose(handle)

    recorded = min(int(header.dwBytesRecorded), capacity)
    if recorded < SAMPLE_RATE // 2:
        raise RuntimeError("Nagranie jest zbyt krótkie.")
    if recorded > MAX_AUDIO_BYTES:
        raise RuntimeError("Nagranie przekracza limit rozmiaru.")
    pcm = ctypes.string_at(audio_buffer, recorded)
    import io
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return output.getvalue()


def _is_editable(obj):
    if obj is None:
        return False
    candidate = obj
    for _depth in range(5):
        if candidate is None:
            break
        try:
            if getattr(candidate, "isEditable", False):
                return True
        except Exception:
            pass
        try:
            if candidate.role == controlTypes.Role.EDITABLETEXT:
                return True
        except Exception:
            pass
        try:
            if controlTypes.State.EDITABLE in candidate.states:
                return True
        except Exception:
            pass
        # Windows 11 Notepad exposes its RichEdit document through UIA as a
        # DOCUMENT in some NVDA/provider combinations, without EDITABLE state.
        # Restrict this fallback to Notepad's document object, not its toolbar.
        try:
            app_name = str(candidate.appModule.appName or "").lower()
            if app_name == "notepad":
                if candidate.role == controlTypes.Role.DOCUMENT:
                    return True
                class_name = str(candidate.windowClassName or "").lower()
                if class_name in ("edit", "richedit", "richeditd2dpt", "richedit50w"):
                    return True
        except Exception:
            pass
        try:
            candidate = candidate.parent
        except Exception:
            break
    return False


def _focus_identity(obj):
    try:
        return int(obj.windowHandle)
    except Exception:
        return None


def _transcription_prompt(language, rich_punctuation, gender_precision, background_protection="standard"):
    lang = "auto-detect" if language == "auto" else language
    prompt = (
        "Transcribe the intended dictation in this recording accurately. "
        "Source language code: {}. Return only a faithful transcription. "
        "Infer sentence boundaries and punctuation from meaning, grammar, pauses and intonation; "
        "the speaker does not need to say punctuation marks aloud. "
    ).format(lang)
    if rich_punctuation:
        prompt += (
            "Use natural, context-sensitive punctuation, including colons, semicolons, dashes, "
            "quotation marks, parentheses, ellipses, questions and exclamations when appropriate. "
            "Do not overuse punctuation or change meaning. "
        )
    if gender_precision and (language == "pl" or language == "auto"):
        prompt += (
            "For Polish speech, preserve the grammatical-gender endings actually spoken. "
            "Distinguish forms such as robiłeś/robiłaś, zrobiłem/zrobiłam and byłeś/byłaś "
            "from the audio and reliable context. Do not guess gender when genuinely ambiguous. "
        )
    if background_protection == "strong":
        prompt += (
            "Transcribe only the primary near-field speaker intentionally dictating. Ignore speech "
            "from a television, radio, loudspeaker, another room or distant people, including during "
            "pauses. If unclear whether words are from the close speaker or background, omit them. "
        )
    elif background_protection == "standard":
        prompt += (
            "Focus on the close speaker intentionally dictating. Ignore obvious television, radio, "
            "loudspeaker and distant background speech. "
        )
    prompt += (
        "Do not answer questions, translate, comment or add emoji. Preserve profanity faithfully "
        "at this stage."
    )
    return prompt


def _postprocess_prompt(text, settings):
    parts = [
        "You are a careful editor of speech transcription.",
        "Preserve the user's meaning, personal style, language and intent. Never translate.",
    ]
    if settings.get("smart_correction"):
        parts.append(
            "Remove stutters, accidental repetitions and obvious slips of the tongue. "
            "Fix only unambiguous grammatical mistakes caused by spontaneous speech. "
            "Do not paraphrase, change opinions or polish beyond the correction needed "
            "to make the intended sentence natural."
        )
    else:
        parts.append("Do not rewrite grammar or style beyond obvious punctuation.")
    if settings.get("rich_punctuation"):
        parts.append(
            "Infer punctuation from meaning, grammar, sentence structure, pauses and intonation. "
            "Use the appropriate mark when warranted: colon, semicolon, dash, parentheses, "
            "quotation marks, ellipsis, question mark or exclamation mark. Avoid comma splices "
            "and over-formatting."
        )
    else:
        parts.append("Use ordinary natural punctuation.")
    if settings.get("gender_precision") and settings.get("language") in ("pl", "auto"):
        parts.append(
            "For Polish, preserve the masculine or feminine grammatical endings actually spoken; "
            "do not default automatically to masculine and do not guess if the audio is ambiguous."
        )
    if settings.get("smart_names"):
        parts.append(
            "Normalize phonetically spoken proper names and specialist terms to canonical spelling "
            "when context makes the intended name clear. Do not guess aggressively."
        )
    if settings.get("auto_emoji"):
        parts.append(
            "Add one or two fitting emoji when the message has a clear emotional or social tone. "
            "For joy, thanks, greetings, congratulations, affection, humor or celebration, include "
            "at least one suitable emoji; place it naturally, usually at the end of the sentence. "
            "Do not add emoji to neutral, formal or technical text, and never replace words with emoji."
        )
    else:
        parts.append("Do not add emoji that the user did not dictate.")
    if settings.get("smart_paragraphs"):
        parts.append(
            "Keep a short single-thought message in one paragraph. Divide longer speech into "
            "logical paragraphs separated by exactly one blank line. Do not add headings, bullets, "
            "Markdown or decorative separators."
        )
    profanity = settings.get("profanity_mode", "unchanged")
    if profanity == "soften":
        parts.append("Replace profanity with natural milder equivalents without changing meaning.")
    elif profanity == "censor":
        parts.append("Mask profanity with asterisks where practical; preserve the sentence and meaning.")
    else:
        parts.append("Preserve profanity as spoken.")
    dictionary = str(settings.get("dictionary", "")).strip()
    if dictionary:
        parts.append(
            "Apply these exact user dictionary substitutions after correction when the spoken term "
            "matches clearly. Do not alter unrelated words:\n" + dictionary
        )
    parts.append(
        "Return only the final text, without commentary or Markdown. Do not wrap it in quotation marks.\n\n"
        "TRANSCRIPTION:\n{}".format(text)
    )
    return " ".join(parts)


def _translation_prompt(text, settings):
    target = settings.get("target_language", "en")
    language_names = {
        "pl": "Polish", "en": "English", "de": "German", "es": "Spanish",
        "fr": "French", "it": "Italian", "uk": "Ukrainian", "ru": "Russian", "zh": "Chinese",
    }
    parts = [
        "Translate the source text into {} (language code {}).".format(language_names.get(target, target), target),
        "Preserve meaning, tone, proper names, numbers and natural punctuation. Return only the translation.",
    ]
    if settings.get("smart_correction"):
        parts.append(
            "Before translating, remove stutters, accidental repetitions and only obvious speech-recognition "
            "errors. Do not paraphrase or change the meaning."
        )
    if settings.get("smart_names"):
        parts.append("Normalize phonetically spoken names and specialist terms only when the intended spelling is clear.")
    if settings.get("smart_paragraphs"):
        parts.append("Keep a short thought in one paragraph; split longer text into coherent paragraphs.")
    if settings.get("auto_emoji"):
        parts.append("Use relevant emoji sparingly when they fit the speaker's tone.")
    profanity = settings.get("profanity_mode", "unchanged")
    if profanity == "soften":
        parts.append("Replace profanity with natural milder equivalents without changing meaning.")
    elif profanity == "censor":
        parts.append("Mask profanity with asterisks where practical while preserving the sentence.")
    else:
        parts.append("Preserve profanity.")
    parts.append("SOURCE TEXT:\n" + str(text))
    return " ".join(parts)


def _chat_prompt(text, history, settings):
    parts = [
        "Have a natural, helpful conversation. Reply in the same language as the latest user message unless asked otherwise.",
        "Be concrete and relevant. Do not reveal private reasoning. Return only the response.",
    ]
    if settings.get("smart_paragraphs"):
        parts.append("Split long answers into short logical paragraphs for a screen reader.")
    profanity = settings.get("profanity_mode", "unchanged")
    if profanity == "soften":
        parts.append("Use natural milder equivalents for profanity while preserving meaning.")
    elif profanity == "censor":
        parts.append("Mask profanity with asterisks while preserving meaning.")
    if history:
        rendered_history = "\n".join("Użytkownik: {}\nAsystent: {}".format(user, assistant) for user, assistant in history)
        parts.append("RECENT CONVERSATION:\n" + rendered_history)
    parts.append("NEW USER MESSAGE:\n" + str(text))
    return "\n\n".join(parts)


def _apply_dictionary(text, dictionary):
    result = str(text or "")
    for line in str(dictionary or "").splitlines():
        if "=>" not in line:
            continue
        source, replacement = line.split("=>", 1)
        source = source.strip()
        replacement = replacement.strip()
        if source:
            import re
            result = re.sub(re.escape(source), lambda _m: replacement, result, flags=re.IGNORECASE)
    return result


def _gemini_request(api_key, model, contents):
    model = (model or DEFAULT_MODEL).strip().removeprefix("models/")
    url = GEMINI_ENDPOINT.format(model=urllib.parse.quote(model, safe="-._"))
    body = json.dumps({"contents": [{"parts": contents}]}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        details = e.read().decode("utf-8", "replace")
        raise RuntimeError("Gemini zwrócił HTTP {}: {}".format(e.code, details[:1200]))
    except urllib.error.URLError as e:
        raise RuntimeError("Nie udało się połączyć z Gemini: {}".format(e.reason))
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini nie zwrócił tekstu.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
    text = text.strip()
    if not text:
        raise RuntimeError("Gemini zwrócił pusty tekst.")
    return text


def _http_json(url, headers=None, data=None, method=None, timeout=REQUEST_TIMEOUT):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        details = e.read().decode("utf-8", "replace")
        raise RuntimeError("HTTP {}: {}".format(e.code, details[:1200]))
    except urllib.error.URLError as e:
        raise RuntimeError("Błąd połączenia: {}".format(e.reason))
    return raw, content_type


def _xai_chat(api_key, model, prompt, temperature=0.15):
    body = json.dumps(
        {
            "model": model or "grok-4.7",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    raw, _ = _http_json(
        "https://api.x.ai/v1/chat/completions",
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        data=body,
        method="POST",
    )
    result = json.loads(raw.decode("utf-8"))
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError("Grok nie zwrócił odpowiedzi.")
    text = ((choices[0].get("message") or {}).get("content") or "").strip()
    if isinstance(text, list):
        text = "".join(str(x.get("text", "")) for x in text if isinstance(x, dict))
    if not text:
        raise RuntimeError("Grok zwrócił pustą odpowiedź.")
    return text


def _openai_response(api_key, model, prompt, images=None):
    content = [{"type": "input_text", "text": str(prompt)}]
    for image in images or []:
        encoded = base64.b64encode(image).decode("ascii")
        content.append({"type": "input_image", "image_url": "data:{};base64,".format(_image_mime(image)) + encoded})
    body = json.dumps(
        {"model": model or DEFAULT_OPENAI_MODEL, "input": [{"role": "user", "content": content}]},
        ensure_ascii=False,
    ).encode("utf-8")
    raw, _ = _http_json(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        data=body,
        method="POST",
    )
    result = json.loads(raw.decode("utf-8"))
    text = str(result.get("output_text", "") or "").strip()
    if not text:
        pieces = []
        for item in result.get("output", []):
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    pieces.append(str(part.get("text", "")))
        text = "\n".join(pieces).strip()
    if not text:
        raise RuntimeError("OpenAI API nie zwróciło tekstu.")
    return text


def _openai_transcribe(api_key, audio, language, prompt=""):
    boundary = "----NVDAFormBoundary" + binascii.hexlify(os.urandom(12)).decode("ascii")
    chunks = []
    fields = [("model", OPENAI_TRANSCRIPTION_MODEL)]
    if language and language != "auto":
        fields.append(("language", language))
    if prompt:
        fields.append(("prompt", prompt))
    for name, value in fields:
        chunks.extend([
            ("--" + boundary + "\r\n").encode("ascii"),
            ('Content-Disposition: form-data; name="{}"\r\n\r\n'.format(name)).encode("ascii"),
            str(value).encode("utf-8") + b"\r\n",
        ])
    chunks.extend([
        ("--" + boundary + "\r\n").encode("ascii"),
        b'Content-Disposition: form-data; name="file"; filename="dictation.wav"\r\n',
        b"Content-Type: audio/wav\r\n\r\n",
        audio,
        b"\r\n--" + boundary.encode("ascii") + b"--\r\n",
    ])
    raw, _ = _http_json(
        "https://api.openai.com/v1/audio/transcriptions",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "Accept": "application/json",
        },
        data=b"".join(chunks),
        method="POST",
    )
    result = json.loads(raw.decode("utf-8"))
    transcript = str(result.get("text", "")).strip()
    if not transcript:
        raise RuntimeError("OpenAI API nie zwróciło transkrypcji.")
    return transcript


def _xai_stt(api_key, audio, language):
    boundary = "----NVDAFormBoundary" + binascii.hexlify(os.urandom(12)).decode("ascii")
    chunks = []
    fields = [
        ("model", "grok-voice-transcribe-2.0"),
        ("format", "true"),
    ]
    if language and language != "auto":
        fields.append(("language", language))
    for name, value in fields:
        chunks.extend([
            ("--" + boundary + "\r\n").encode("ascii"),
            ('Content-Disposition: form-data; name="{}"\r\n\r\n'.format(name)).encode("ascii"),
            str(value).encode("utf-8") + b"\r\n",
        ])
    chunks.extend([
        ("--" + boundary + "\r\n").encode("ascii"),
        b'Content-Disposition: form-data; name="file"; filename="dictation.wav"\r\n',
        b"Content-Type: audio/wav\r\n\r\n",
        audio,
        b"\r\n--" + boundary.encode("ascii") + b"--\r\n",
    ])
    raw, _ = _http_json(
        "https://api.x.ai/v1/stt",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "Accept": "application/json",
        },
        data=b"".join(chunks),
        method="POST",
    )
    result = json.loads(raw.decode("utf-8"))
    transcript = str(result.get("text", "")).strip()
    if not transcript:
        raise RuntimeError("xAI nie zwróciło transkrypcji.")
    return transcript


def _xai_vision(api_key, model, prompt, images):
    content = [{"type": "input_text", "text": prompt}]
    for image in images:
        content.append({"type": "input_image", "image_url": "data:{};base64,".format(_image_mime(image)) + base64.b64encode(image).decode("ascii")})
    body = json.dumps({"model": model or "grok-4.7", "input": [{"role": "user", "content": content}]}, ensure_ascii=False).encode("utf-8")
    raw, _ = _http_json("https://api.x.ai/v1/responses", headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}, data=body, method="POST")
    result = json.loads(raw.decode("utf-8"))
    pieces = []
    for item in result.get("output", []):
        for part in item.get("content", []):
            if part.get("type") in ("output_text", "text"):
                pieces.append(str(part.get("text", "")))
    text = "\n".join(pieces).strip()
    if not text:
        raise RuntimeError("Grok nie zwrócił opisu.")
    return text


def _gemini_vision(api_key, model, prompt, images):
    contents = [{"text": prompt}]
    for image in images:
        contents.append({"inlineData": {"mimeType": _image_mime(image), "data": base64.b64encode(image).decode("ascii")}})
    return _gemini_request(api_key, model, contents)


def _image_mime(image):
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(image) >= 12 and image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    raise RuntimeError("Nieobsługiwany format obrazu. Wybierz PNG, JPG albo WebP.")


def _read_attached_image(path):
    with open(path, "rb") as image_file:
        data = image_file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise RuntimeError("Plik obrazu jest większy niż 10 MB.")
    _image_mime(data)
    return data


def _find_ffmpeg_tools(configured_path=""):
    configured_path = str(configured_path or "").strip().strip('"')
    addon_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled_dir = os.path.join(addon_root, "bin", "ffmpeg")
    bundled_ffmpeg = os.path.join(bundled_dir, "ffmpeg.exe")
    bundled_ffprobe = os.path.join(bundled_dir, "ffprobe.exe")
    if os.path.isfile(bundled_ffmpeg) and os.path.isfile(bundled_ffprobe):
        return bundled_ffmpeg, bundled_ffprobe
    if configured_path and os.path.isfile(configured_path):
        configured_probe = os.path.join(os.path.dirname(configured_path), "ffprobe.exe")
        if os.path.isfile(configured_probe):
            return configured_path, configured_probe
    ffmpeg = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe.exe") or shutil.which("ffprobe")
    if ffmpeg and not ffprobe:
        sibling = os.path.join(os.path.dirname(ffmpeg), "ffprobe.exe")
        if os.path.isfile(sibling):
            ffprobe = sibling
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    for directory in (
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "ffmpeg", "bin"),
        r"C:\ffmpeg\bin",
        r"C:\Program Files\FFmpeg\bin",
    ):
        candidate_ffmpeg = os.path.join(directory, "ffmpeg.exe")
        candidate_ffprobe = os.path.join(directory, "ffprobe.exe")
        if os.path.isfile(candidate_ffmpeg) and os.path.isfile(candidate_ffprobe):
            return candidate_ffmpeg, candidate_ffprobe
    raise RuntimeError("Nie znaleziono dołączonych programów do odczytu wideo ani FFmpeg i FFprobe w PATH.")


def _extract_video_frames(path, max_frames, ffmpeg_path=""):
    ffmpeg, ffprobe = _find_ffmpeg_tools(ffmpeg_path)
    try:
        probe = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True,
        )
        duration = float(probe.stdout.decode("ascii", "ignore").strip())
    except Exception as e:
        raise RuntimeError("Nie udało się odczytać długości filmu przez FFmpeg: {}".format(str(e)[:120]))
    frame_count = max(1, min(int(max_frames), 15))
    fps = min(1.0, frame_count / max(duration, 0.1))
    with tempfile.TemporaryDirectory(prefix="polskieDyktowanie_video_") as directory:
        pattern = os.path.join(directory, "klatka_%03d.png")
        try:
            subprocess.run(
                [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
                 "-vf", "fps={:.8f},scale=1280:1280:force_original_aspect_ratio=decrease".format(fps),
                 "-frames:v", str(frame_count), pattern],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=True,
            )
        except subprocess.CalledProcessError as e:
            detail = e.stderr.decode("utf-8", "replace")[-300:].strip() if e.stderr else ""
            raise RuntimeError("FFmpeg nie może odczytać tego filmu. {}".format(detail))
        except subprocess.TimeoutExpired:
            raise RuntimeError("Analiza filmu przez FFmpeg trwała zbyt długo.")
        frames = []
        for filename in sorted(os.listdir(directory)):
            if filename.lower().endswith(".png"):
                with open(os.path.join(directory, filename), "rb") as frame_file:
                    frames.append(frame_file.read())
        if not frames:
            raise RuntimeError("FFmpeg nie znalazł klatek w wybranym pliku wideo.")
        return frames


def _extract_audio_segments(path, output_directory, segment_seconds=300):
    ffmpeg, _ffprobe = _find_ffmpeg_tools()
    pattern = os.path.join(output_directory, "audio_%03d.wav")
    try:
        subprocess.run(
            [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", path,
             "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
             "-f", "segment", "-segment_time", str(int(segment_seconds)), "-reset_timestamps", "1", pattern],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900, check=True,
        )
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode("utf-8", "replace")[-400:].strip() if e.stderr else ""
        raise RuntimeError("Nie udało się odczytać ścieżki dźwiękowej. Sprawdź, czy plik zawiera mowę. {}".format(detail))
    except subprocess.TimeoutExpired:
        raise RuntimeError("Przygotowanie nagrania do transkrypcji trwało zbyt długo.")
    segments = sorted(
        os.path.join(output_directory, name)
        for name in os.listdir(output_directory)
        if name.startswith("audio_") and name.lower().endswith(".wav")
    )
    if not segments:
        raise RuntimeError("Nie znaleziono dźwięku w wybranym pliku.")
    return segments


def _gemini_tts(api_key, model, voice, text):
    body = json.dumps({
        "model": model or "gemini-3.1-flash-tts-preview",
        "input": text,
        "response_format": {"type": "audio", "mime_type": "audio/wav"},
        "generation_config": {"speech_config": [{"voice": voice or "Kore"}]},
    }, ensure_ascii=False).encode("utf-8")
    raw, _ = _http_json("https://generativelanguage.googleapis.com/v1beta/interactions", headers={"x-goog-api-key": api_key, "Content-Type": "application/json", "Api-Revision": "2026-05-20"}, data=body, method="POST")
    payload = json.loads(raw.decode("utf-8"))
    audio = None
    mime = "audio/wav"
    audio_output = payload.get("output_audio") or {}
    if isinstance(audio_output, dict) and audio_output.get("data"):
        audio = audio_output["data"]
        mime = audio_output.get("mime_type") or audio_output.get("mimeType") or mime
    for item in payload.get("outputs", []):
        if isinstance(item, dict) and item.get("type") in ("audio", "audio_output"):
            candidate = item.get("data") or item.get("audio")
            if candidate:
                audio = candidate
                mime = item.get("mime_type") or item.get("mimeType") or mime
                break
    if not audio:
        raise RuntimeError("Gemini TTS nie zwrócił danych audio.")
    return base64.b64decode(audio), mime


def _xai_tts(api_key, voice, text):
    body = json.dumps({"text": text, "voice_id": voice or "eve", "language": "pl", "output_format": {"codec": "mp3", "sample_rate": 24000, "bit_rate": 128000}}, ensure_ascii=False).encode("utf-8")
    raw, content_type = _http_json("https://api.x.ai/v1/tts", headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json", "Accept": "audio/mpeg, application/json"}, data=body, method="POST")
    if "json" in content_type.lower():
        payload = json.loads(raw.decode("utf-8"))
        audio = payload.get("audio") or payload.get("data")
        if isinstance(audio, str):
            raw = base64.b64decode(audio)
    return raw, content_type or "audio/mpeg"


def _openai_tts(api_key, model, voice, text):
    if len(text) > 4096:
        raise RuntimeError("OpenAI TTS przyjmuje do 4096 znaków naraz. Zaznacz krótszy tekst.")
    body = json.dumps({
        "model": model or "gpt-4o-mini-tts",
        "input": text,
        "voice": voice or "marin",
        "response_format": "mp3",
    }, ensure_ascii=False).encode("utf-8")
    audio, content_type = _http_json(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        data=body,
        method="POST",
    )
    return audio, content_type or "audio/mpeg"


def _play_audio_bytes(audio, mime):
    """Play returned audio; use a short-lived temporary file for Windows playback APIs."""
    suffix = ".wav" if "wav" in mime.lower() else ".mp3"
    if suffix == ".wav" and not audio.startswith(b"RIFF"):
        # Gemini PCM output is 24 kHz, mono, signed 16-bit little-endian.
        audio = b"RIFF" + struct.pack("<I", 36 + len(audio)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16) + b"data" + struct.pack("<I", len(audio)) + audio
    with tempfile.NamedTemporaryFile(prefix="polskie_dyktowanie_", suffix=suffix, delete=False) as file:
        path = file.name
        file.write(audio)
    try:
        if suffix == ".wav":
            winsound.PlaySound(path, winsound.SND_FILENAME)
        else:
            mci = ctypes.WinDLL("winmm", use_last_error=True)
            command = mci.mciSendStringW
            command.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, ctypes.c_void_p]
            command.restype = wintypes.UINT
            alias = "PolskieDyktowanieTTS"
            if command('open "{}" type mpegvideo alias {}'.format(path, alias), None, 0, None):
                raise RuntimeError("Windows nie może odtworzyć pliku TTS.")
            try:
                command("play {} wait".format(alias), None, 0, None)
            finally:
                command("close {}".format(alias), None, 0, None)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _bitmap_to_png(width, height, bgra):
    """Convert top-down BGRA pixels to a minimal RGB PNG using only stdlib."""
    rows = []
    stride = width * 4
    for y in range(height):
        source = bgra[y * stride:(y + 1) * stride]
        rgb = bytearray(width * 3)
        for x in range(width):
            si = x * 4
            di = x * 3
            rgb[di:di + 3] = source[si + 2:si + 3] + source[si + 1:si + 2] + source[si:si + 1]
        rows.append(b"\x00" + bytes(rgb))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b"")


def _focused_image_region(focus):
    """Return the on-screen rectangle of the focused accessible graphic."""
    graphic_role = getattr(controlTypes.Role, "GRAPHIC", None)
    candidate = focus
    for _depth in range(10):
        if candidate is None:
            break
        try:
            role = getattr(candidate, "role", None)
        except Exception:
            role = None
        role_name = str(role).lower()
        if role == graphic_role or "graphic" in role_name or "image" in role_name:
            try:
                location = candidate.location
                rect = (
                    int(location.left), int(location.top),
                    int(location.width), int(location.height),
                )
                if rect[2] >= 24 and rect[3] >= 24:
                    return rect
            except Exception:
                pass
        try:
            candidate = candidate.parent
        except Exception:
            break
    raise RuntimeError("Aby opisać sam obraz, ustaw fokus NVDA na elemencie graficznym albo jego zawartości. W innym przypadku wybierz „Opis całego ekranu”.")


def _capture_desktop_png(region=None):
    """Capture the virtual desktop or a screen region through GDI as PNG bytes."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.BitBlt.restype = wintypes.BOOL
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
        ]
    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.restype = wintypes.BOOL

    virtual_left = user32.GetSystemMetrics(76)
    virtual_top = user32.GetSystemMetrics(77)
    virtual_width = user32.GetSystemMetrics(78)
    virtual_height = user32.GetSystemMetrics(79)
    if region is None:
        left, top = virtual_left, virtual_top
        width, height = virtual_width, virtual_height
    else:
        requested_left, requested_top, requested_width, requested_height = region
        virtual_right = virtual_left + virtual_width
        virtual_bottom = virtual_top + virtual_height
        left = max(virtual_left, requested_left)
        top = max(virtual_top, requested_top)
        right = min(virtual_right, requested_left + requested_width)
        bottom = min(virtual_bottom, requested_top + requested_height)
        width, height = right - left, bottom - top
    if width <= 0 or height <= 0 or width * height > 20_000_000:
        raise RuntimeError("Nieprawidłowy rozmiar pulpitu do przechwycenia.")
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    if not screen or not memory or not bitmap:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory:
            gdi32.DeleteDC(memory)
        if screen:
            user32.ReleaseDC(None, screen)
        raise ctypes.WinError(ctypes.get_last_error())
    old = gdi32.SelectObject(memory, bitmap)
    try:
        if not gdi32.BitBlt(memory, 0, 0, width, height, screen, left, top, 0x00CC0020 | 0x40000000):
            raise ctypes.WinError(ctypes.get_last_error())
        gdi32.SelectObject(memory, old)
        info = BITMAPINFO()
        info.bmiHeader = BITMAPINFOHEADER(
            ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0,
            width * height * 4, 0, 0, 0, 0,
        )
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(screen, bitmap, 0, height, buffer, ctypes.byref(info), 0)
        if rows != height:
            raise RuntimeError("Nie udało się odczytać pikseli pulpitu.")
        return _bitmap_to_png(width, height, buffer.raw)
    finally:
        user32.ReleaseDC(None, screen)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)


class _SettingsDialog(wx.Dialog):
    MASKED_KEY = "************"
    PROVIDERS = ["Gemini / Google", "Grok / xAI", "OpenAI API"]
    PROVIDER_IDS = ["gemini", "grok", "openai"]
    MODES = ["Dyktowanie", "Inteligentny czat", "Tłumaczenie", "Tekst na mowę"]
    MODE_IDS = ["dictation", "chat", "translate", "tts"]
    VISUAL_MODES = ["Opis obrazu z pliku", "Opis obrazu z fokusu NVDA", "Opis całego ekranu", "Rozpoznawanie kolorów na ekranie", "Opis wideo z pliku", "Opis wideo z ekranu"]
    VISUAL_MODE_IDS = ["photo_file", "photo", "screen", "colors", "video_file", "video"]
    GEMINI_TTS_VOICES = [
        ("Zephyr — jasny", "Zephyr"), ("Puck — żywy", "Puck"), ("Charon — informacyjny", "Charon"),
        ("Kore — stanowczy", "Kore"), ("Fenrir — pełen energii", "Fenrir"), ("Leda — młodzieńczy", "Leda"),
        ("Orus — stanowczy", "Orus"), ("Aoede — swobodny", "Aoede"), ("Callirrhoe — spokojny", "Callirrhoe"),
        ("Autonoe — jasny", "Autonoe"), ("Enceladus — oddechowy", "Enceladus"), ("Iapetus — wyraźny", "Iapetus"),
        ("Umbriel — swobodny", "Umbriel"), ("Algieba — gładki", "Algieba"), ("Despina — gładki", "Despina"),
        ("Erinome — wyraźny", "Erinome"), ("Algenib — chropowaty", "Algenib"), ("Rasalgethi — informacyjny", "Rasalgethi"),
        ("Laomedeia — żywy", "Laomedeia"), ("Achernar — łagodny", "Achernar"), ("Alnilam — stanowczy", "Alnilam"),
        ("Schedar — stonowany", "Schedar"), ("Gacrux — dojrzały", "Gacrux"), ("Pulcherrima — zdecydowany", "Pulcherrima"),
        ("Achird — przyjazny", "Achird"), ("Zubenelgenubi — swobodny", "Zubenelgenubi"),
        ("Vindemiatrix — łagodny", "Vindemiatrix"), ("Sadachbia — żywy", "Sadachbia"),
        ("Sadaltager — kompetentny", "Sadaltager"), ("Sulafat — ciepły", "Sulafat"),
    ]
    XAI_TTS_VOICES = [
        ("Ara — ciepły i przyjazny", "ara"), ("Eve — energiczny i żywy", "eve"),
        ("Leo — autorytatywny i mocny", "leo"), ("Rex — pewny i wyraźny", "rex"),
        ("Sal — gładki i zrównoważony", "sal"), ("Rigel — precyzyjny i profesjonalny", "rigel"),
        ("Cosmo — jasny i ciekawy", "cosmo"), ("Celeste — spokojny i wspierający", "celeste"),
        ("Ursa — przyjazny i ciepły", "ursa"), ("Sirius — dowcipny i figlarny", "sirius"),
        ("Lumen — ciepły i wyrazisty", "lumen"), ("Castor — swobodny i naturalny", "castor"),
        ("Naksh — spokojny i refleksyjny", "naksh"), ("Atlas — pewny i stanowczy", "atlas"),
        ("Aurora — spokojny i łagodny", "aurora"), ("Liora — ciepły i kojący", "liora"),
    ]
    OPENAI_TTS_VOICES = [(voice, voice) for voice in (
        "alloy", "ash", "ballad", "coral", "echo", "fable", "marin", "cedar", "nova", "onyx", "sage", "shimmer", "verse"
    )]
    SHORTCUTS = [
        ("Alt+Q", "kb:alt+q"),
        ("Alt+Shift+Q", "kb:alt+shift+q"),
        ("Ctrl+Alt+Q", "kb:control+alt+q"),
        ("Ctrl+Shift+Q", "kb:control+shift+q"),
        ("Ctrl+Alt+Shift+Q", "kb:control+alt+shift+q"),
        ("Alt+F8", "kb:alt+f8"),
        ("Ctrl+Alt+F8", "kb:control+alt+f8"),
        ("Ctrl+Alt+Shift+F1", "kb:control+alt+shift+f1"),
        ("Ctrl+Alt+Shift+F2", "kb:control+alt+shift+f2"),
        ("Ctrl+Alt+Shift+F3", "kb:control+alt+shift+f3"),
        ("Ctrl+Alt+Shift+F4", "kb:control+alt+shift+f4"),
        ("Ctrl+Alt+Shift+F5", "kb:control+alt+shift+f5"),
        ("Ctrl+Alt+Shift+F6", "kb:control+alt+shift+f6"),
        ("Ctrl+Alt+Shift+F7", "kb:control+alt+shift+f7"),
        ("Ctrl+Alt+Shift+F8", "kb:control+alt+shift+f8"),
        ("Ctrl+Alt+Shift+F9", "kb:control+alt+shift+f9"),
        ("Ctrl+Alt+Shift+F10", "kb:control+alt+shift+f10"),
    ]
    VISUAL_SHORTCUT_FIELDS = {
        "photo_file": "shortcut_visual_photo_file",
        "photo": "shortcut_visual_photo",
        "screen": "shortcut_visual_screen",
        "colors": "shortcut_visual_colors",
        "video_file": "shortcut_visual_video_file",
        "video": "shortcut_visual_video",
    }
    LANGUAGES = [
        ("Polski", "pl"), ("Automatycznie", "auto"), ("English", "en"),
        ("Deutsch", "de"), ("Español", "es"), ("Français", "fr"),
        ("Italiano", "it"), ("Українська", "uk"), ("Русский", "ru"),
        ("中文", "zh"),
    ]

    def __init__(self, parent, settings, save_callback, test_callback, fetch_callback, run_callback, copy_callback, read_callback, clear_callback, file_action_callback):
        super().__init__(
            parent,
            title="Polskie Dyktowanie — ustawienia",
            size=(720, 680),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.STAY_ON_TOP,
        )
        self._settings = dict(settings)
        self._vision_model_choices = {}
        self._save_callback = save_callback
        self._test_callback = test_callback
        self._fetch_callback = fetch_callback
        self._run_callback = run_callback
        self._copy_callback = copy_callback
        self._read_callback = read_callback
        self._clear_callback = clear_callback
        self._file_action_callback = file_action_callback

        panel = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)
        notebook = wx.Notebook(panel)
        scroll = None
        content = None

        def new_page(title):
            nonlocal scroll, content
            if scroll is not None:
                scroll.SetSizer(content)
            scroll = wx.ScrolledWindow(notebook, style=wx.VSCROLL)
            scroll.SetScrollRate(0, 12)
            content = wx.BoxSizer(wx.VERTICAL)
            notebook.AddPage(scroll, title)

        def section(title):
            label = wx.StaticText(scroll, label=title)
            font = label.GetFont()
            font.SetWeight(wx.FONTWEIGHT_BOLD)
            label.SetFont(font)
            content.Add(label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        def row(label, control):
            # Keep labels immediately before their controls in the accessibility
            # tree. Side-by-side nested sizers caused NVDA to announce some
            # Choice values separately from the label that described them.
            try:
                control.SetName(label)
            except Exception:
                pass
            label_control = wx.StaticText(scroll, label=label)
            content.Add(label_control, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
            content.Add(control, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 3)

        new_page("Ogólne")
        section("Silnik i tryb")
        self.provider = wx.RadioBox(
            scroll,
            label="Dostawca AI do tekstu, obrazu i wideo",
            choices=self.PROVIDERS,
            majorDimension=1,
            style=wx.RA_SPECIFY_ROWS,
        )
        selected_provider = settings.get("provider", "gemini")
        self.provider.SetSelection(self.PROVIDER_IDS.index(selected_provider) if selected_provider in self.PROVIDER_IDS else 0)
        content.Add(self.provider, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.active_api_key_label = wx.StaticText(scroll, label="")
        content.Add(self.active_api_key_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.active_api_key = wx.TextCtrl(scroll, style=wx.TE_PASSWORD)
        self.active_api_key.SetHint("Wklej klucz API dla wybranego dostawcy")
        content.Add(self.active_api_key, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 3)
        self.active_api_key_status = wx.StaticText(scroll, label="")
        self.active_api_key_status.Wrap(600)
        content.Add(self.active_api_key_status, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.active_api_key.Bind(wx.EVT_TEXT, self._update_active_key_status)
        self.active_api_key.Bind(wx.EVT_SET_FOCUS, self._select_masked_key)
        self._update_active_key_controls()
        self.provider.Bind(wx.EVT_RADIOBOX, self._on_provider)
        self.mode = wx.Choice(scroll, choices=self.MODES)
        selected_mode = settings.get("mode", "dictation")
        self.mode.SetSelection(self.MODE_IDS.index(selected_mode) if selected_mode in self.MODE_IDS else 0)
        row("Tryb:", self.mode)
        author_info = wx.StaticText(
            scroll,
            label="Autor Polskiego Dyktanda: Madridista/Realista. Adaptacja dodatku dla NVDA.",
        )
        author_info.Wrap(650)
        content.Add(author_info, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)

        new_page("Modele Gemini")
        section("Modele tekstowe Gemini / Google")
        gemini_model = str(settings.get("model", DEFAULT_MODEL)) or DEFAULT_MODEL
        self.gemini_model = wx.Choice(scroll, choices=[gemini_model])
        self.gemini_model.SetSelection(0)
        row("Model Gemini do dyktowania i tekstu:", self.gemini_model)
        fetch_gemini = wx.Button(scroll, label="Pobierz listę modeli Gemini")
        fetch_gemini.Bind(wx.EVT_BUTTON, lambda _event: self._on_fetch("gemini"))
        content.Add(fetch_gemini, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        new_page("Modele Grok / xAI")
        section("Modele tekstowe Grok / xAI")
        grok_model = str(settings.get("grok_model", "grok-4.7")) or "grok-4.7"
        self.grok_model = wx.Choice(scroll, choices=[grok_model])
        self.grok_model.SetSelection(0)
        row("Model Grok do dyktowania i tekstu:", self.grok_model)
        fetch_grok = wx.Button(scroll, label="Pobierz listę modeli Grok / xAI")
        fetch_grok.Bind(wx.EVT_BUTTON, lambda _event: self._on_fetch("grok"))
        content.Add(fetch_grok, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)

        new_page("Modele OpenAI")
        section("Modele tekstowe OpenAI API")
        openai_text_model = str(settings.get("openai_text_model", settings.get("openai_model", DEFAULT_OPENAI_MODEL))) or DEFAULT_OPENAI_MODEL
        self.openai_text_model = wx.Choice(scroll, choices=[openai_text_model])
        self.openai_text_model.SetSelection(0)
        row("Model OpenAI do dyktowania, czatu i tłumaczenia:", self.openai_text_model)
        fetch_openai_text = wx.Button(scroll, label="Pobierz modele OpenAI do tekstu")
        fetch_openai_text.Bind(wx.EVT_BUTTON, lambda _event: self._on_fetch("openai_text"))
        content.Add(fetch_openai_text, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        openai_note = wx.StaticText(
            scroll,
            label="Klucz OpenAI wpisuje się na karcie „Ogólne”. Tutaj wybierasz model do tekstu. Model obrazu i wideo wybierzesz na karcie „Obraz”. Transkrypcja używa osobnego modelu gpt-transcribe.",
        )
        openai_note.Wrap(650)
        content.Add(openai_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        new_page("Mowa na tekst")
        section("Transkrypcja mowy na tekst")
        stt_file_note = wx.StaticText(scroll, label="Możesz dyktować skrótem F7 albo wskazać nagranie audio/wideo. Transkrypcja zostanie zapisana jako plik TXT.")
        stt_file_note.Wrap(650)
        content.Add(stt_file_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.transcribe_file_button = wx.Button(scroll, label="Wybierz nagranie i zapisz transkrypcję…")
        self.transcribe_file_button.Bind(wx.EVT_BUTTON, lambda _event: self._on_file_action("transcribe"))
        content.Add(self.transcribe_file_button, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.language = wx.Choice(scroll, choices=[item[0] for item in self.LANGUAGES])
        self.language.SetSelection(max(0, next((i for i, item in enumerate(self.LANGUAGES) if item[1] == settings.get("language", "pl")), 0)))
        row("Język mowy:", self.language)
        self.target_language = wx.Choice(scroll, choices=[item[0] for item in self.LANGUAGES if item[1] not in ("auto",)])
        target_codes = [item[1] for item in self.LANGUAGES if item[1] != "auto"]
        self.target_language.SetSelection(max(0, target_codes.index(settings.get("target_language", "en")) if settings.get("target_language", "en") in target_codes else 0))
        row("Język tłumaczenia:", self.target_language)

        checks = [
            ("smart_correction", "Inteligentna korekta mowy"),
            ("gender_precision", "Pilnowanie polskich form żeńskich i męskich"),
            ("smart_names", "Poprawianie nazw własnych i terminów"),
            ("auto_emoji", "Dodawaj emoji do serdecznych lub emocjonalnych wypowiedzi"),
            ("smart_paragraphs", "Układanie dłuższych wypowiedzi w akapity"),
            ("rich_punctuation", "Rozbudowana, naturalna interpunkcja"),
            ("read_result", "Odczytaj wynik po wstawieniu"),
            ("auto_send", "Po wstawieniu naciśnij Enter (może wysłać formularz)"),
            ("system_dictation", "Użyj Windows Voice Typing zamiast AI dla dyktowania"),
        ]
        self.checks = {}
        for key, label in checks:
            control = wx.CheckBox(scroll, label=label)
            control.SetValue(bool(settings.get(key)))
            self.checks[key] = control
            content.Add(control, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)

        self.profanity = wx.RadioBox(
            scroll,
            label="Postępowanie z wulgaryzmami",
            choices=["Bez zmian", "Łagodź", "Cenzuruj"],
            majorDimension=1,
            style=wx.RA_SPECIFY_ROWS,
        )
        self.profanity.SetSelection({"unchanged": 0, "soften": 1, "censor": 2}.get(settings.get("profanity_mode"), 0))
        content.Add(self.profanity, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.background = wx.RadioBox(
            scroll,
            label="Ochrona przed mową z tła",
            choices=["Wyłączona", "Standardowa", "Silna"],
            majorDimension=1,
            style=wx.RA_SPECIFY_ROWS,
        )
        self.background.SetSelection({"off": 0, "standard": 1, "strong": 2}.get(settings.get("background_voice_protection"), 1))
        content.Add(self.background, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.separator = wx.RadioBox(
            scroll,
            label="Separator wstawiany przed nowym tekstem dyktowania",
            choices=["Spacja", "Nowy wiersz", "Bez separatora"],
            majorDimension=1,
            style=wx.RA_SPECIFY_ROWS,
        )
        self.separator.SetSelection({"space": 0, "newline": 1, "none": 2}.get(settings.get("separator"), 0))
        content.Add(self.separator, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.dictionary = wx.TextCtrl(scroll, value=str(settings.get("dictionary", "")), style=wx.TE_MULTILINE, size=(-1, 90))
        self.dictionary.SetName("Słownik zamian nazw i terminów")
        self.dictionary.SetHint("Jedna zamiana w wierszu, np. gemini => Gemini. Puste pole wyłącza słownik.")
        dictionary_note = wx.StaticText(
            scroll,
            label="To opcjonalne reguły poprawiania nazw własnych i terminów, które dyktowanie rozpoznaje błędnie. Wpisz jedną regułę w każdym wierszu, np. gemini => Gemini. Jeśli nie potrzebujesz własnych poprawek, pozostaw pole puste.",
        )
        dictionary_note.Wrap(650)
        content.Add(dictionary_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        dictionary_group = wx.StaticBoxSizer(wx.VERTICAL, scroll, "Własne poprawki nazw i terminów — opcjonalnie")
        dictionary_group.Add(self.dictionary, 1, wx.EXPAND | wx.ALL, 8)
        content.Add(dictionary_group, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)

        new_page("Obraz")
        section("Obraz")
        self.vision_model_label = wx.StaticText(scroll, label="")
        content.Add(self.vision_model_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        self.vision_model = wx.Choice(scroll, choices=[])
        content.Add(self.vision_model, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 3)
        self.fetch_vision_models_button = wx.Button(scroll, label="")
        self.fetch_vision_models_button.Bind(wx.EVT_BUTTON, self._on_fetch_vision_models)
        content.Add(self.fetch_vision_models_button, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        vision_model_note = wx.StaticText(scroll, label="Dostawca i klucz z karty „Ogólne”. Model obrazu wybierasz tutaj.")
        vision_model_note.Wrap(650)
        content.Add(vision_model_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.visual_mode = wx.Choice(scroll, choices=self.VISUAL_MODES)
        selected_visual_mode = settings.get("visual_mode", "screen")
        self.visual_mode.SetSelection(max(0, self.VISUAL_MODE_IDS.index(selected_visual_mode) if selected_visual_mode in self.VISUAL_MODE_IDS else 0))
        row("Rodzaj analizy uruchamianej skrótem:", self.visual_mode)
        visual_mode_note = wx.StaticText(scroll, label="Pliki nie wymagają fokusu. Opis ekranu i kolorów analizuje pulpit.")
        visual_mode_note.Wrap(650)
        content.Add(visual_mode_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        attachment_note = wx.StaticText(scroll, label="Skrót opisu pliku zawsze otwiera wybór nowego obrazu lub filmu.")
        content.Add(attachment_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)
        self.detail = wx.Choice(scroll, choices=["Krótki opis", "Szczegółowy opis"])
        self.detail.SetSelection(0 if settings.get("vision_detail") == "short" else 1)
        row("Szczegółowość opisu obrazu, ekranu lub wideo:", self.detail)
        self.video_seconds = wx.Choice(scroll, choices=["5", "8", "12", "15"])
        duration = str(settings.get("video_seconds", 8))
        self.video_seconds.SetSelection(max(0, ["5", "8", "12", "15"].index(duration) if duration in ["5", "8", "12", "15"] else 1))
        row("Czas wideo z ekranu / limit klatek z pliku:", self.video_seconds)
        video_duration_note = wx.StaticText(scroll, label="Dla ekranu: sekundy. Dla pliku: liczba klatek z całego filmu.")
        video_duration_note.Wrap(650)
        content.Add(video_duration_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        ffmpeg_note = wx.StaticText(scroll, label="Obsługa plików wideo jest dołączona do dodatku. Nie musisz instalować ani wskazywać osobnych programów.")
        ffmpeg_note.Wrap(650)
        content.Add(ffmpeg_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)
        new_page("Tekst na mowę")
        section("Ustawienia syntezy mowy")
        tts_file_note = wx.StaticText(scroll, label="Możesz odczytać zaznaczony tekst skrótem F8 albo wczytać plik TXT. Z niego powstanie plik audio WAV.")
        tts_file_note.Wrap(650)
        content.Add(tts_file_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.speak_file_button = wx.Button(scroll, label="Wybierz plik tekstowy i zapisz mowę jako WAV…")
        self.speak_file_button.Bind(wx.EVT_BUTTON, lambda _event: self._on_file_action("synthesize_file"))
        content.Add(self.speak_file_button, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.tts_provider_page = scroll
        self.tts_provider_note = wx.StaticText(scroll, label="Synteza używa silnika wybranego na karcie „Ogólne”.")
        self.tts_provider_note.Wrap(650)
        content.Add(self.tts_provider_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self.gemini_tts_panel = wx.Panel(scroll)
        gemini_tts_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.gemini_tts_panel, "Gemini / Google")
        gemini_tts_model = str(settings.get("gemini_tts_model", "gemini-3.1-flash-tts-preview")) or "gemini-3.1-flash-tts-preview"
        self.gemini_tts_model = wx.Choice(self.gemini_tts_panel, choices=[gemini_tts_model])
        self.gemini_tts_model.SetSelection(0)
        gemini_tts_sizer.Add(wx.StaticText(self.gemini_tts_panel, label="Model do syntezy mowy:"), 0, wx.TOP, 6)
        gemini_tts_sizer.Add(self.gemini_tts_model, 0, wx.EXPAND | wx.TOP, 3)
        self.fetch_tts_models = wx.Button(self.gemini_tts_panel, label="Pobierz modele Gemini TTS")
        self.fetch_tts_models.Bind(wx.EVT_BUTTON, lambda _event: self._on_fetch("gemini_tts"))
        gemini_tts_sizer.Add(self.fetch_tts_models, 0, wx.TOP, 7)
        gemini_tts_sizer.Add(wx.StaticText(self.gemini_tts_panel, label="Głos:"), 0, wx.TOP, 8)
        gemini_voice_ids = [voice_id for _label, voice_id in self.GEMINI_TTS_VOICES]
        self.gemini_tts_voice = wx.Choice(self.gemini_tts_panel, choices=[label for label, _voice_id in self.GEMINI_TTS_VOICES])
        selected_gemini_voice = str(settings.get("gemini_tts_voice", "Kore") or "Kore")
        self.gemini_tts_voice.SetSelection(gemini_voice_ids.index(selected_gemini_voice) if selected_gemini_voice in gemini_voice_ids else gemini_voice_ids.index("Kore"))
        self.gemini_tts_voice.SetName("Głos Gemini TTS")
        gemini_tts_sizer.Add(self.gemini_tts_voice, 0, wx.EXPAND | wx.TOP, 3)
        self.gemini_tts_panel.SetSizer(gemini_tts_sizer)
        content.Add(self.gemini_tts_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self.grok_tts_panel = wx.Panel(scroll)
        grok_tts_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.grok_tts_panel, "Grok / xAI")
        grok_tts_note = wx.StaticText(self.grok_tts_panel, label="Usługa xAI TTS sama dobiera model. Głos wybierz z listy.")
        grok_tts_note.Wrap(620)
        grok_tts_sizer.Add(grok_tts_note, 0, wx.EXPAND | wx.TOP, 6)
        grok_tts_sizer.Add(wx.StaticText(self.grok_tts_panel, label="Głos:"), 0, wx.TOP, 6)
        xai_voice_ids = [voice_id for _label, voice_id in self.XAI_TTS_VOICES]
        self.xai_tts_voice = wx.Choice(self.grok_tts_panel, choices=[label for label, _voice_id in self.XAI_TTS_VOICES])
        selected_xai_voice = str(settings.get("xai_tts_voice", "eve") or "eve").lower()
        self.xai_tts_voice.SetSelection(xai_voice_ids.index(selected_xai_voice) if selected_xai_voice in xai_voice_ids else xai_voice_ids.index("eve"))
        self.xai_tts_voice.SetName("Głos Grok / xAI")
        grok_tts_sizer.Add(self.xai_tts_voice, 0, wx.EXPAND | wx.TOP, 3)
        self.grok_tts_panel.SetSizer(grok_tts_sizer)
        content.Add(self.grok_tts_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self.openai_tts_panel = wx.Panel(scroll)
        openai_tts_sizer = wx.StaticBoxSizer(wx.VERTICAL, self.openai_tts_panel, "OpenAI API")
        selected_openai_tts_model = str(settings.get("openai_tts_model", "gpt-4o-mini-tts") or "gpt-4o-mini-tts")
        self.openai_tts_model = wx.Choice(self.openai_tts_panel, choices=[selected_openai_tts_model])
        self.openai_tts_model.SetSelection(0)
        openai_tts_sizer.Add(wx.StaticText(self.openai_tts_panel, label="Model do syntezy mowy:"), 0, wx.TOP, 6)
        openai_tts_sizer.Add(self.openai_tts_model, 0, wx.EXPAND | wx.TOP, 3)
        self.fetch_openai_tts_models = wx.Button(self.openai_tts_panel, label="Pobierz modele OpenAI TTS")
        self.fetch_openai_tts_models.Bind(wx.EVT_BUTTON, lambda _event: self._on_fetch("openai_tts"))
        openai_tts_sizer.Add(self.fetch_openai_tts_models, 0, wx.TOP, 7)
        openai_tts_sizer.Add(wx.StaticText(self.openai_tts_panel, label="Głos:"), 0, wx.TOP, 8)
        openai_tts_voices = [voice_id for _label, voice_id in self.OPENAI_TTS_VOICES]
        self.openai_tts_voice = wx.Choice(self.openai_tts_panel, choices=[label for label, _voice_id in self.OPENAI_TTS_VOICES])
        selected_openai_voice = str(settings.get("openai_tts_voice", "marin") or "marin")
        self.openai_tts_voice.SetSelection(openai_tts_voices.index(selected_openai_voice) if selected_openai_voice in openai_tts_voices else 0)
        self.openai_tts_voice.SetName("Głos OpenAI TTS")
        openai_tts_sizer.Add(self.openai_tts_voice, 0, wx.EXPAND | wx.TOP, 3)
        openai_tts_note = wx.StaticText(self.openai_tts_panel, label="OpenAI API: maksymalnie 4096 znaków na żądanie; dłuższy tekst jest dzielony na fragmenty. Model gpt-4o-mini-tts ma zapowiedziane wycofanie.")
        openai_tts_note.Wrap(620)
        openai_tts_sizer.Add(openai_tts_note, 0, wx.EXPAND | wx.TOP, 8)
        self.openai_tts_panel.SetSizer(openai_tts_sizer)
        content.Add(self.openai_tts_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        new_page("Skróty")
        section("Skróty klawiaturowe")
        main_shortcut_choices = ["Skrót 1 — dyktowanie i ustawienia: {}".format(label) for label, _identifier in self.SHORTCUTS]
        self.shortcut_main = wx.Choice(scroll, choices=main_shortcut_choices)
        self.shortcut_main.SetSelection(self._shortcut_index(settings.get("shortcut_main", "kb:alt+q")))
        content.Add(self.shortcut_main, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        global_shortcut_choices = ["Skrót 2 — obraz i wideo: {}".format(label) for label, _identifier in self.SHORTCUTS]
        self.shortcut_global = wx.Choice(scroll, choices=global_shortcut_choices)
        self.shortcut_global.SetSelection(self._shortcut_index(settings.get("shortcut_global", "kb:alt+shift+q")))
        content.Add(self.shortcut_global, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        stt_shortcut_choices = ["Skrót mowa na tekst — dyktowanie: {}".format(label) for label, _identifier in self.SHORTCUTS]
        self.shortcut_speech_to_text = wx.Choice(scroll, choices=stt_shortcut_choices)
        self.shortcut_speech_to_text.SetSelection(self._shortcut_index(settings.get("shortcut_speech_to_text", _DEFAULTS["shortcut_speech_to_text"])))
        content.Add(self.shortcut_speech_to_text, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        tts_shortcut_choices = ["Skrót tekst na mowę — synteza: {}".format(label) for label, _identifier in self.SHORTCUTS]
        self.shortcut_text_to_speech = wx.Choice(scroll, choices=tts_shortcut_choices)
        self.shortcut_text_to_speech.SetSelection(self._shortcut_index(settings.get("shortcut_text_to_speech", _DEFAULTS["shortcut_text_to_speech"])))
        content.Add(self.shortcut_text_to_speech, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        file_stt_choices = ["Wybierz plik audio/wideo i zapisz transkrypcję: {}".format(label) for label, _identifier in self.SHORTCUTS]
        self.shortcut_file_transcription = wx.Choice(scroll, choices=file_stt_choices)
        self.shortcut_file_transcription.SetSelection(self._shortcut_index(settings.get("shortcut_file_transcription", _DEFAULTS["shortcut_file_transcription"])))
        self.shortcut_file_transcription.SetName("Skrót transkrypcji pliku audio lub wideo")
        content.Add(self.shortcut_file_transcription, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        file_tts_choices = ["Wybierz plik TXT i zapisz mowę jako WAV: {}".format(label) for label, _identifier in self.SHORTCUTS]
        self.shortcut_file_synthesis = wx.Choice(scroll, choices=file_tts_choices)
        self.shortcut_file_synthesis.SetSelection(self._shortcut_index(settings.get("shortcut_file_synthesis", _DEFAULTS["shortcut_file_synthesis"])))
        self.shortcut_file_synthesis.SetName("Skrót syntezy mowy z pliku tekstowego")
        content.Add(self.shortcut_file_synthesis, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        shortcut_note = wx.StaticText(scroll, label="Skrót 1 zachowuje dotychczasowe działanie. Skróty dyktowania i syntezy zaznaczonego tekstu działają bezpośrednio. Dwa nowe skróty otwierają wybór pliku: audio/wideo do transkrypcji albo TXT do syntezy WAV. Skrót 2 uruchamia wybrany tryb opisu. Pozostałe skróty uruchamiają wskazany rodzaj opisu. Podczas nagrywania użyj skrótu dyktowania lub skrótu 1, aby zakończyć.")
        shortcut_note.Wrap(650)
        content.Add(shortcut_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.visual_shortcuts = {}
        for mode_id, field in self.VISUAL_SHORTCUT_FIELDS.items():
            index = self.VISUAL_MODE_IDS.index(mode_id)
            label = "Skrót — {}:".format(self.VISUAL_MODES[index])
            content.Add(wx.StaticText(scroll, label=label), 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
            choice = wx.Choice(scroll, choices=[entry[0] for entry in self.SHORTCUTS])
            default = _DEFAULTS[field]
            choice.SetSelection(self._shortcut_index(settings.get(field, default)))
            choice.SetName(label.rstrip(":"))
            content.Add(choice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 3)
            self.visual_shortcuts[mode_id] = choice

        note = wx.StaticText(scroll, label="Nagranie i zrzuty ekranu są wysyłane do wybranego dostawcy AI.")
        note.Wrap(650)
        content.Add(note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 12)
        scroll.SetSizer(content)
        outer.Add(notebook, 1, wx.EXPAND | wx.ALL, 8)

        action_row = wx.BoxSizer(wx.HORIZONTAL)
        for label, callback in [
            ("Testuj API", self._on_test),
            ("Uruchom tryb", self._on_run),
            ("Skopiuj ostatni wynik", self._copy_callback),
            ("Odczytaj ostatni wynik", self._read_callback),
            ("Wyczyść log", self._clear_callback),
        ]:
            button = wx.Button(panel, label=label)
            button.Bind(wx.EVT_BUTTON, lambda event, cb=callback: cb())
            action_row.Add(button, 0, wx.RIGHT, 5)
        outer.Add(action_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        buttons = self.CreateButtonSizer(wx.OK | wx.CANCEL)
        panel.SetSizer(outer)
        self._update_vision_model_controls()
        # The content controls belong to the panel, while CreateButtonSizer
        # creates its buttons as children of the dialog. Give each sizer a
        # matching parent so the controls are sized and shown correctly.
        dialog_sizer = wx.BoxSizer(wx.VERTICAL)
        dialog_sizer.Add(panel, 1, wx.EXPAND)
        dialog_sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(dialog_sizer)
        self.Layout()
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self._update_tts_controls()

    def collect(self):
        settings = dict(self._settings)
        active_provider = self.PROVIDER_IDS[max(0, self.provider.GetSelection())]
        active_key_name, _provider_title = self._provider_key_info(active_provider)
        active_key_value = self.active_api_key.GetValue().strip()
        if active_key_value and active_key_value != self.MASKED_KEY:
            settings[active_key_name] = active_key_value
        settings["provider"] = active_provider
        settings["mode"] = self.MODE_IDS[max(0, self.mode.GetSelection())]
        settings["visual_mode"] = self.VISUAL_MODE_IDS[max(0, self.visual_mode.GetSelection())]
        settings["visual_mode_schema"] = 1
        settings["model"] = self.gemini_model.GetStringSelection().strip() or DEFAULT_MODEL
        settings["grok_model"] = self.grok_model.GetStringSelection().strip() or "grok-4.7"
        settings["openai_text_model"] = self.openai_text_model.GetStringSelection().strip() or DEFAULT_OPENAI_MODEL
        vision_provider = self.PROVIDER_IDS[max(0, self.provider.GetSelection())]
        vision_key, vision_default, _display, _button_name = self._vision_model_setting(vision_provider)
        settings[vision_key] = self.vision_model.GetStringSelection().strip() or vision_default
        settings["language"] = self.LANGUAGES[max(0, self.language.GetSelection())][1]
        target_codes = [item[1] for item in self.LANGUAGES if item[1] != "auto"]
        settings["target_language"] = target_codes[max(0, self.target_language.GetSelection())]
        for key, control in self.checks.items():
            settings[key] = bool(control.GetValue())
        settings["profanity_mode"] = ["unchanged", "soften", "censor"][max(0, self.profanity.GetSelection())]
        settings["background_voice_protection"] = ["off", "standard", "strong"][max(0, self.background.GetSelection())]
        settings["separator"] = ["space", "newline", "none"][max(0, self.separator.GetSelection())]
        settings["dictionary"] = self.dictionary.GetValue()
        settings["vision_detail"] = "short" if self.detail.GetSelection() == 0 else "detailed"
        settings["video_seconds"] = int(self.video_seconds.GetStringSelection() or "8")
        settings["tts_provider"] = active_provider
        settings["gemini_tts_model"] = self.gemini_tts_model.GetStringSelection().strip() or "gemini-3.1-flash-tts-preview"
        settings["gemini_tts_voice"] = self.GEMINI_TTS_VOICES[max(0, self.gemini_tts_voice.GetSelection())][1]
        settings["xai_tts_voice"] = self.XAI_TTS_VOICES[max(0, self.xai_tts_voice.GetSelection())][1]
        settings["openai_tts_model"] = self.openai_tts_model.GetStringSelection().strip() or "gpt-4o-mini-tts"
        settings["openai_tts_voice"] = self.OPENAI_TTS_VOICES[max(0, self.openai_tts_voice.GetSelection())][1]
        settings["shortcut_main"] = self.SHORTCUTS[max(0, self.shortcut_main.GetSelection())][1]
        settings["shortcut_global"] = self.SHORTCUTS[max(0, self.shortcut_global.GetSelection())][1]
        settings["shortcut_speech_to_text"] = self.SHORTCUTS[max(0, self.shortcut_speech_to_text.GetSelection())][1]
        settings["shortcut_text_to_speech"] = self.SHORTCUTS[max(0, self.shortcut_text_to_speech.GetSelection())][1]
        settings["shortcut_file_transcription"] = self.SHORTCUTS[max(0, self.shortcut_file_transcription.GetSelection())][1]
        settings["shortcut_file_synthesis"] = self.SHORTCUTS[max(0, self.shortcut_file_synthesis.GetSelection())][1]
        for mode_id, field in self.VISUAL_SHORTCUT_FIELDS.items():
            choice = self.visual_shortcuts[mode_id]
            settings[field] = self.SHORTCUTS[max(0, choice.GetSelection())][1]
        return settings

    def _update_tts_panels(self, provider):
        self.gemini_tts_panel.Show(provider == "gemini")
        self.grok_tts_panel.Show(provider == "grok")
        self.openai_tts_panel.Show(provider == "openai")
        self.tts_provider_page.Layout()

    def _update_tts_controls(self):
        if not hasattr(self, "gemini_tts_panel"):
            return
        active_provider = self.PROVIDER_IDS[max(0, self.provider.GetSelection())]
        active_value = self.active_api_key.GetValue().strip()
        key_name, provider_title = self._provider_key_info(active_provider)
        has_key = bool(self._settings.get(key_name)) or bool(active_value and active_value != self.MASKED_KEY)
        self._settings["tts_provider"] = active_provider
        self.tts_provider_note.SetLabel(
            "Synteza używa silnika {} wybranego na karcie „Ogólne”.".format(provider_title)
            if has_key else
            "Wpisz klucz API {} na karcie „Ogólne”, aby użyć tego silnika do syntezy mowy.".format(provider_title)
        )
        self._update_tts_panels(active_provider if has_key else "")

    def _shortcut_index(self, identifier):
        return max(0, next((i for i, (_label, value) in enumerate(self.SHORTCUTS) if value == identifier), 0))

    def _vision_model_setting(self, provider):
        settings = {
            "gemini": ("gemini_vision_model", DEFAULT_GEMINI_VISION_MODEL, "Gemini / Google", "Gemini"),
            "grok": ("grok_vision_model", DEFAULT_GROK_VISION_MODEL, "Grok / xAI", "Grok / xAI"),
            "openai": ("openai_vision_model", DEFAULT_OPENAI_VISION_MODEL, "OpenAI API", "OpenAI"),
        }
        key, default, display, button_name = settings.get(provider, settings["gemini"])
        return key, default, display, button_name

    def _update_vision_model_controls(self):
        provider = self.PROVIDER_IDS[max(0, self.provider.GetSelection())]
        key, default, display, button_name = self._vision_model_setting(provider)
        value = str(self._settings.get(key, default) or default)
        choices = self._vision_model_choices.get(provider, [value])
        if value not in choices:
            choices = [value] + list(choices)
        self.vision_model.SetItems(choices)
        self.vision_model.SetStringSelection(value)
        self.vision_model_label.SetLabel("Model {} do obrazu, ekranu, kolorów i wideo:".format(display))
        self.fetch_vision_models_button.SetLabel("Pobierz modele {} do obrazu i wideo".format(button_name))

    def _store_vision_model_selection(self, provider):
        key, default, _display, _button_name = self._vision_model_setting(provider)
        value = self.vision_model.GetStringSelection().strip() or default
        self._settings[key] = value
        return key, value

    def _on_fetch_vision_models(self, _event):
        provider = self.PROVIDER_IDS[max(0, self.provider.GetSelection())]
        self._on_fetch("vision_{}".format(provider))

    def _provider_key_info(self, provider):
        return {
            "gemini": ("gemini_key", "Gemini / Google"),
            "grok": ("xai_key", "Grok / xAI"),
            "openai": ("openai_key", "OpenAI API"),
        }.get(provider, ("gemini_key", "Gemini / Google"))

    def _update_active_key_controls(self):
        provider = self.PROVIDER_IDS[max(0, self.provider.GetSelection())]
        key_name, provider_title = self._provider_key_info(provider)
        self.active_api_key_label.SetLabel("Klucz API {}: ".format(provider_title))
        if self._settings.get(key_name):
            self.active_api_key.SetValue(self.MASKED_KEY)
        else:
            self.active_api_key.SetValue("")
        self._update_active_key_status()

    def _update_active_key_status(self, _event=None):
        provider = self.PROVIDER_IDS[max(0, self.provider.GetSelection())]
        key_name, provider_title = self._provider_key_info(provider)
        value = self.active_api_key.GetValue().strip()
        if value and value != self.MASKED_KEY:
            self.active_api_key_status.SetLabel("Wpisano nowy klucz {}. Zostanie zapisany po zatwierdzeniu ustawień.".format(provider_title))
        elif self._settings.get(key_name):
            self.active_api_key_status.SetLabel("Klucz {} jest zapisany i chroniony. Wpisanie nowego klucza go zastąpi.".format(provider_title))
        else:
            self.active_api_key_status.SetLabel("Brak klucza {}. Wklej klucz API i zatwierdź ustawienia.".format(provider_title))
        self._update_tts_controls()

    def _select_masked_key(self, event):
        control = event.GetEventObject()
        if control.GetValue() == self.MASKED_KEY:
            control.SelectAll()
        event.Skip()

    def set_model_choices(self, models, provider):
        if provider == "gemini_tts":
            key = "gemini_tts_model"
            control = self.gemini_tts_model
            default = "gemini-3.1-flash-tts-preview"
        elif provider == "openai_tts":
            key = "openai_tts_model"
            control = self.openai_tts_model
            default = "gpt-4o-mini-tts"
        elif provider in ("openai_text", "openai"):
            key = "openai_text_model"
            control = self.openai_text_model
            default = DEFAULT_OPENAI_MODEL
        elif provider.startswith("vision_"):
            vision_provider = provider.split("_", 1)[1]
            key, default, _display, _button_name = self._vision_model_setting(vision_provider)
            control = self.vision_model
        else:
            key = "model" if provider == "gemini" else "grok_model"
            control = self.gemini_model if provider == "gemini" else self.grok_model
            default = DEFAULT_MODEL if provider == "gemini" else "grok-4.7"
        selected = control.GetStringSelection() or str(self._settings.get(key, default))
        values = list(dict.fromkeys([selected] + [str(model) for model in models if model]))
        control.SetItems(values)
        control.SetStringSelection(selected)
        self._settings[key] = selected
        if provider.startswith("vision_"):
            self._vision_model_choices[vision_provider] = values
            self._update_vision_model_controls()
        ui.message("Pobrano {} modeli. Wybierz model z listy.".format(len(models)))

    def _on_provider(self, event):
        old_provider = self._settings.get("provider", "gemini")
        old_key_name, _old_provider_title = self._provider_key_info(old_provider)
        active_key_value = self.active_api_key.GetValue().strip()
        updates = {}
        if active_key_value and active_key_value != self.MASKED_KEY:
            self._settings[old_key_name] = active_key_value
            updates[old_key_name] = active_key_value
        key, value = self._store_vision_model_selection(old_provider)
        provider = self.PROVIDER_IDS[max(0, self.provider.GetSelection())]
        self._update_vision_model_controls()
        self._update_active_key_controls()
        self._settings["provider"] = provider
        # Persist immediately so Cancel or closing the dialog cannot silently
        # switch the active engine back for dictation, translation, or vision.
        updates.update({"provider": provider, key: value})
        self._save_callback(updates)

    def _on_ok(self, event):
        settings = self._collect_validated()
        if settings is None:
            return
        self._save_callback(settings, announce=True)
        self.EndModal(wx.ID_OK)

    def _collect_validated(self):
        settings = self.collect()
        shortcut_fields = ["shortcut_main", "shortcut_global", "shortcut_speech_to_text", "shortcut_text_to_speech", "shortcut_file_transcription", "shortcut_file_synthesis"] + list(self.VISUAL_SHORTCUT_FIELDS.values())
        shortcuts = [settings[field] for field in shortcut_fields]
        if len(shortcuts) != len(set(shortcuts)):
            wx.MessageBox("Każda akcja musi mieć inny skrót. Zmień powtarzające się przypisania.", "Polskie Dyktowanie", wx.OK | wx.ICON_WARNING, self)
            return None
        return settings

    def _on_test(self):
        settings = self._collect_validated()
        if settings is None:
            return
        self._save_callback(settings)
        self._test_callback()

    def _on_fetch(self, provider):
        settings = self._collect_validated()
        if settings is None:
            return
        self._save_callback(settings)
        self._fetch_callback(provider)

    def _on_run(self):
        settings = self._collect_validated()
        if settings is None:
            return
        self._save_callback(settings)
        self.EndModal(wx.ID_OK)
        wx.CallAfter(self._run_callback)

    def _on_file_action(self, action):
        settings = self._collect_validated()
        if settings is None:
            return
        self._save_callback(settings)
        self.EndModal(wx.ID_OK)
        wx.CallAfter(self._file_action_callback, action, settings)


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = "Polskie Dyktowanie"

    def __init__(self):
        super().__init__()
        self._base_settings = dict(_DEFAULTS)
        self._settings = self._load_settings()
        self._recording = False
        self._processing = False
        self._stop_event = None
        self._target_hwnd = None
        self._settings_dialog = None
        self._last_result = ""
        self._chat_history = []
        self._bind_shortcuts()

    def terminate(self):
        if self._stop_event is not None:
            self._stop_event.set()
        super().terminate()

    def _config_dir(self):
        import globalVars
        path = os.path.join(globalVars.appArgs.configPath, ADDON_NAME)
        os.makedirs(path, exist_ok=True)
        return path

    def _settings_path(self):
        return os.path.join(self._config_dir(), "settings.json")

    def _load_settings(self):
        settings = dict(_DEFAULTS)
        try:
            with open(self._settings_path(), "r", encoding="utf-8") as file:
                saved = json.load(file)
            settings.update({k: v for k, v in saved.items() if k in settings})
            if "gemini_vision_model" not in saved and saved.get("model"):
                settings["gemini_vision_model"] = saved["model"]
            if "grok_vision_model" not in saved and saved.get("grok_model"):
                settings["grok_vision_model"] = saved["grok_model"]
            legacy_openai_model = str(saved.get("openai_model", "") or "")
            if legacy_openai_model:
                if "openai_text_model" not in saved:
                    settings["openai_text_model"] = legacy_openai_model
                if "openai_vision_model" not in saved:
                    settings["openai_vision_model"] = legacy_openai_model
            # Reset the shortcut pair once because older releases allowed the
            # visual shortcut to be confused with the main dictation shortcut.
            if int(saved.get("shortcut_assignment_version", 0) or 0) < 1:
                settings["shortcut_main"] = "kb:alt+q"
                settings["shortcut_global"] = "kb:alt+shift+q"
                settings["shortcut_assignment_version"] = 1
            # Older releases stored image, color and video modes in the same
            # field as dictation. Migrate them to the dedicated visual mode so
            # opening settings remains safe and the visual shortcut works.
            if "visual_mode" not in saved and saved.get("mode") in ("photo", "colors", "video"):
                settings["visual_mode"] = saved["mode"]
                settings["mode"] = "dictation"
            if int(saved.get("visual_mode_schema", 0) or 0) < 1:
                # Previous "photo" mode captured the full desktop; preserve
                # that behavior as the new explicit screen-description mode.
                if settings.get("visual_mode") == "photo":
                    settings["visual_mode"] = "screen"
                settings["visual_mode_schema"] = 1
            gemini_protected = settings.get("gemini_key_protected") or saved.get("api_key_protected") or ""
            xai_protected = settings.get("xai_key_protected") or ""
            openai_protected = settings.get("openai_key_protected") or ""
            settings["gemini_key"] = _dpapi(base64.b64decode(gemini_protected), False).decode("utf-8") if gemini_protected else ""
            settings["xai_key"] = _dpapi(base64.b64decode(xai_protected), False).decode("utf-8") if xai_protected else ""
            settings["openai_key"] = _dpapi(base64.b64decode(openai_protected), False).decode("utf-8") if openai_protected else ""
        except FileNotFoundError:
            settings["gemini_key"] = ""
            settings["xai_key"] = ""
            settings["openai_key"] = ""
        except Exception:
            settings["gemini_key"] = ""
            settings["xai_key"] = ""
            settings["openai_key"] = ""
            self._write_log("Nie udało się odczytać ustawień; sprawdź plik settings.json.")
        return settings

    def _save_settings(self, settings, announce=False):
        shortcut_fields = ["shortcut_main", "shortcut_global", "shortcut_speech_to_text", "shortcut_text_to_speech", "shortcut_file_transcription", "shortcut_file_synthesis"] + list(_SettingsDialog.VISUAL_SHORTCUT_FIELDS.values())
        old_shortcuts = tuple(self._settings.get(field) for field in shortcut_fields)
        merged = dict(self._settings)
        merged.update(settings)
        gemini_key = str(merged.get("gemini_key", ""))
        xai_key = str(merged.get("xai_key", ""))
        openai_key = str(merged.get("openai_key", ""))
        gemini_protected = base64.b64encode(_dpapi(gemini_key.encode("utf-8"), True)).decode("ascii") if gemini_key else ""
        xai_protected = base64.b64encode(_dpapi(xai_key.encode("utf-8"), True)).decode("ascii") if xai_key else ""
        openai_protected = base64.b64encode(_dpapi(openai_key.encode("utf-8"), True)).decode("ascii") if openai_key else ""
        stored = {k: merged.get(k) for k in _DEFAULTS if k not in ("gemini_key_protected", "xai_key_protected")}
        stored["gemini_key_protected"] = gemini_protected
        stored["xai_key_protected"] = xai_protected
        stored["openai_key_protected"] = openai_protected
        path = self._settings_path()
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as file:
            json.dump(stored, file, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
        merged["gemini_key_protected"] = gemini_protected
        merged["xai_key_protected"] = xai_protected
        merged["openai_key_protected"] = openai_protected
        self._settings = merged
        if old_shortcuts != tuple(merged.get(field) for field in shortcut_fields):
            self._bind_shortcuts()
        if announce:
            ui.message("Ustawienia Polskiego Dyktowania zapisane.")

    def _bind_shortcuts(self):
        try:
            self.clearGestureBindings()
            bindings = [
                (self._settings.get("shortcut_main", "kb:alt+q"), "dictationOrSettings"),
                (self._settings.get("shortcut_global", "kb:alt+shift+q"), "runVisualMode"),
                (self._settings.get("shortcut_speech_to_text", _DEFAULTS["shortcut_speech_to_text"]), "runSpeechToText"),
                (self._settings.get("shortcut_text_to_speech", _DEFAULTS["shortcut_text_to_speech"]), "runTextToSpeech"),
                (self._settings.get("shortcut_file_transcription", _DEFAULTS["shortcut_file_transcription"]), "runFileTranscription"),
                (self._settings.get("shortcut_file_synthesis", _DEFAULTS["shortcut_file_synthesis"]), "runFileSynthesis"),
            ]
            bindings.extend(
                (self._settings.get(field, _DEFAULTS[field]), "runVisual_{}".format(mode_id))
                for mode_id, field in _SettingsDialog.VISUAL_SHORTCUT_FIELDS.items()
            )
            for gesture, script_name in bindings:
                try:
                    self.bindGesture(gesture, script_name)
                except Exception as e:
                    self._write_log("Nie udało się przypisać {} do {}: {}".format(gesture, script_name, e))
        except Exception as e:
            self._write_log("Przypisanie skrótów: {}".format(e))
            try:
                self.clearGestureBindings()
                self.bindGesture("kb:alt+q", "dictationOrSettings")
                self.bindGesture("kb:alt+shift+q", "runVisualMode")
            except Exception:
                pass
            wx.CallAfter(ui.message, "Nie udało się przypisać skrótów; przywrócono domyślne.")

    def _write_log(self, message):
        try:
            path = os.path.join(self._config_dir(), "polskieDyktowanie.log")
            with open(path, "a", encoding="utf-8") as file:
                file.write(str(message).replace("\r", " ").replace("\n", " ")[:2500] + "\n")
        except Exception:
            pass

    def _show_settings(self):
        if self._settings_dialog:
            try:
                self._bring_settings_to_front(self._settings_dialog)
                return
            except Exception:
                self._settings_dialog = None
        self._play_cue(850, 90)
        dialog = None
        popup_started = False
        try:
            # NVDA requires popup lifecycle hooks around modal add-on dialogs.
            gui.mainFrame.prePopup()
            popup_started = True
            dialog = _SettingsDialog(gui.mainFrame, self._settings, self._save_settings, self._test_api, self._fetch_models, self._run_selected_mode, self._copy_last_result, self._read_last_result, self._clear_log, self._run_file_action)
            self._settings_dialog = dialog
            # Activate after ShowModal has displayed the native window.
            wx.CallAfter(self._bring_settings_to_front, dialog)
            dialog.ShowModal()
        except Exception as e:
            self._write_log("Otwieranie ustawień: {}".format(e))
            ui.message("Nie udało się otworzyć ustawień Polskiego Dyktowania. Szczegóły zapisano w logu dodatku.")
        finally:
            if dialog is not None:
                dialog.Destroy()
            self._settings_dialog = None
            if popup_started:
                gui.mainFrame.postPopup()

    def _bring_settings_to_front(self, dialog):
        try:
            dialog.Show(True)
            dialog.Raise()
            hwnd = wintypes.HWND(dialog.GetHandle())
            user32 = ctypes.windll.user32
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            dialog.SetFocus()
        except Exception as e:
            self._write_log("Uaktywnianie ustawień: {}".format(e))

    def _api_key(self, settings):
        provider = settings.get("provider")
        if provider == "grok":
            return settings.get("xai_key", "")
        if provider == "openai":
            return settings.get("openai_key", "")
        return settings.get("gemini_key", "")

    def _model(self, settings):
        provider = settings.get("provider")
        if provider == "grok":
            return settings.get("grok_model", "grok-4.7")
        if provider == "openai":
            return settings.get("openai_text_model", settings.get("openai_model", DEFAULT_OPENAI_MODEL))
        return settings.get("model", DEFAULT_MODEL)

    def _vision_model(self, settings):
        provider = settings.get("provider")
        if provider == "openai":
            return settings.get("openai_vision_model", settings.get("openai_model", DEFAULT_OPENAI_VISION_MODEL))
        if provider == "grok":
            return settings.get("grok_vision_model", settings.get("grok_model", DEFAULT_GROK_VISION_MODEL))
        return settings.get("gemini_vision_model", settings.get("model", DEFAULT_GEMINI_VISION_MODEL))

    def _start(self, focus, mode_override=None):
        settings = dict(self._settings)
        if mode_override:
            settings["mode"] = mode_override
        if settings.get("system_dictation") and settings.get("mode") == "dictation":
            try:
                self._play_cue(720, 100)
                keyboardHandler.KeyboardInputGesture.fromName("windows+h").send()
                return
            except Exception as e:
                self._write_log("Windows Voice Typing: {}".format(e))
        if not self._api_key(settings):
            ui.message("Najpierw ustaw klucz API wybranego dostawcy. Otwieram ustawienia.")
            wx.CallAfter(self._show_settings)
            return
        if settings.get("mode") == "tts":
            self._speak_focus(focus, settings)
            return
        self._target_hwnd = _focus_identity(focus)
        self._stop_event = threading.Event()
        self._recording = True
        self._processing = False
        self._play_cue(720, 100)
        stop_event = self._stop_event
        ui.message("Nagrywanie. Naciśnij ponownie wybrany skrót, aby zakończyć.")
        threading.Thread(
            target=self._record_and_process,
            args=(stop_event, settings),
            name="PolskieDyktowanie",
            daemon=True,
        ).start()

    def _record_and_process(self, stop_event, settings):
        try:
            audio = _record_wav(stop_event)
        except Exception as e:
            self._recording = False
            self._processing = False
            self._write_log("Nagrywanie: {}".format(e))
            wx.CallAfter(self._play_cue, 320, 220)
            wx.CallAfter(ui.message, "Nie udało się nagrać dźwięku: {}".format(str(e)[:180]))
            return
        if not stop_event.is_set():
            wx.CallAfter(self._play_cue, 500, 120)
        self._recording = False
        self._processing = True
        wx.CallAfter(ui.message, "Nagranie zakończone. Przetwarzam.")
        try:
            mode = settings.get("mode", "dictation")
            if mode in ("photo", "screen", "colors", "video"):
                result = self._run_visual(mode, settings)
            else:
                result = self._process_text(mode, self._transcribe(audio, settings), settings)
            if result:
                self._last_result = result
                wx.CallAfter(self._insert_result, result, self._target_hwnd, bool(settings.get("read_result")), settings)
        except Exception as e:
            self._write_log("Dyktowanie: {}".format(e))
            wx.CallAfter(ui.message, "Błąd dyktowania: {}".format(str(e)[:220]))
        finally:
            self._processing = False
            self._stop_event = None

    def _transcribe(self, audio, settings):
        if len(audio) > MAX_AUDIO_BYTES:
            raise RuntimeError("Nagranie przekracza limit rozmiaru.")
        prompt = _transcription_prompt(settings.get("language", "pl"), settings.get("rich_punctuation", True), settings.get("gender_precision", True), settings.get("background_voice_protection", "standard"))
        if settings.get("provider") == "openai":
            return _openai_transcribe(self._api_key(settings), audio, settings.get("language", "pl"), prompt)
        if settings.get("provider") == "grok":
            transcript = _xai_stt(self._api_key(settings), audio, settings.get("language", "pl"))
            return _xai_chat(self._api_key(settings), self._model(settings), prompt + "\n\nTranskrypcja do korekty:\n" + transcript)
        return _gemini_request(self._api_key(settings), self._model(settings), [
            {"text": prompt},
            {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(audio).decode("ascii")}},
        ])

    def _needs_correction(self, settings):
        return any(
            settings.get(key)
            for key in ("smart_correction", "gender_precision", "smart_names", "auto_emoji", "smart_paragraphs", "rich_punctuation")
        ) or settings.get("profanity_mode", "unchanged") != "unchanged"

    def _correct(self, transcript, settings):
        prompt = _postprocess_prompt(transcript, settings)
        if settings.get("provider") == "grok":
            return _xai_chat(self._api_key(settings), self._model(settings), prompt)
        if settings.get("provider") == "openai":
            return _openai_response(self._api_key(settings), self._model(settings), prompt)
        return _gemini_request(self._api_key(settings), self._model(settings), [{"text": prompt}])

    def _process_text(self, mode, text, settings):
        if mode == "dictation":
            output = self._correct(text, settings) if self._needs_correction(settings) else text
            return _apply_dictionary(output, settings.get("dictionary"))
        prompt = _translation_prompt(text, settings) if mode == "translate" else _chat_prompt(text, self._chat_history, settings)
        if settings.get("provider") == "grok":
            output = _xai_chat(self._api_key(settings), self._model(settings), prompt)
        elif settings.get("provider") == "openai":
            output = _openai_response(self._api_key(settings), self._model(settings), prompt)
        else:
            output = _gemini_request(self._api_key(settings), self._model(settings), [{"text": prompt}])
        if mode == "chat":
            self._chat_history.append((text, output))
            self._chat_history = self._chat_history[-6:]
        return _apply_dictionary(output, settings.get("dictionary"))

    def _run_visual(self, mode, settings, focus_rect=None, file_path=None):
        if mode == "photo_file":
            if not file_path:
                raise RuntimeError("Nie wybrano pliku obrazu.")
            frames = [_read_attached_image(file_path)]
        elif mode == "video_file":
            if not file_path:
                raise RuntimeError("Nie wybrano pliku wideo.")
            frames = _extract_video_frames(file_path, settings.get("video_seconds", 8), settings.get("ffmpeg_path", ""))
        else:
            frames = []
            count = max(1, min(int(settings.get("video_seconds", 8)), 15)) if mode == "video" else 1
            if mode == "photo" and not focus_rect:
                raise RuntimeError("Nie znaleziono zaznaczonego obrazu do opisu.")
            for i in range(count):
                if i:
                    time.sleep(1)
                if mode == "photo":
                    frames.append(_capture_desktop_png(focus_rect))
                else:
                    frames.append(_capture_desktop_png())
        detail = "Give a concise description" if settings.get("vision_detail") == "short" else "Give a detailed description"
        if mode == "colors":
            prompt = "Rozpoznaj główne kolory i opisz, gdzie występują (obiekty, tło, pierwszy plan). Zaznacz niepewność, jeśli obraz jest nieczytelny. Odpowiedz po polsku."
        elif mode in ("video", "video_file"):
            source = "klatek pobranych z załączonego filmu" if mode == "video_file" else "zrzutów ekranu pobranych kolejno z krótkiego filmu"
            prompt = "To {} {}. {} Opisz chronologicznie scenę, zmiany, czynności i czytelny tekst. Nie zgaduj dźwięku ani zdarzeń, których nie widać. Odpowiedz po polsku, z myślą o osobie niewidomej.".format(len(frames), source, detail)
        elif mode == "photo_file":
            prompt = "Opisz wyłącznie załączony obraz. Nie opisuj interfejsu ani pulpitu. {} Zacznij od ogólnego obrazu, potem opisz ważne obiekty, ich położenie, widoczny tekst i kontekst. Oddziel obserwacje od niepewności. Odpowiedz po polsku dla osoby niewidomej.".format(detail)
        elif mode == "photo":
            prompt = "Opisz wyłącznie zaznaczony obraz. Zignoruj interfejs programu i elementy pulpitu poza obrazem. {} Zacznij od ogólnego obrazu, potem opisz ważne obiekty, ich położenie, widoczny tekst i kontekst. Oddziel obserwacje od niepewności. Odpowiedz po polsku dla osoby niewidomej.".format(detail)
        else:
            prompt = "Opisz aktualny ekran dla osoby niewidomej. {} Zacznij od ogólnego obrazu, a potem wymień istotne obiekty, ich położenie, widoczny tekst i kontekst. Oddziel obserwacje od niepewności. Odpowiedz po polsku.".format(detail)
        if settings.get("provider") == "grok":
            return _xai_vision(self._api_key(settings), self._vision_model(settings), prompt, frames)
        if settings.get("provider") == "openai":
            return _openai_response(self._api_key(settings), self._vision_model(settings), prompt, frames)
        return _gemini_vision(self._api_key(settings), self._vision_model(settings), prompt, frames)

    def _speak_focus(self, focus, settings):
        text = ""
        try:
            selection = getattr(focus, "selection", None)
            if selection:
                text = selection.text
            if not text:
                text = focus.makeTextInfo("all").text
        except Exception:
            text = getattr(focus, "value", "") or ""
        text = str(text).strip()
        if not text:
            ui.message("Zaznacz tekst lub ustaw fokus w polu zawierającym treść.")
            return
        tts_provider = settings.get("provider", "gemini")
        tts_key = self._api_key(settings)
        if not tts_provider or not tts_key:
            provider_title = {"gemini": "Gemini / Google", "grok": "Grok / xAI", "openai": "OpenAI API"}.get(tts_provider, "wybranego dostawcy")
            ui.message("Synteza mowy wymaga klucza API {}.".format(provider_title))
            wx.CallAfter(self._show_settings)
            return
        settings["tts_provider"] = tts_provider
        self._processing = True
        ui.message("Przygotowuję mowę.")
        threading.Thread(target=self._tts_worker, args=(text, settings), daemon=True, name="PolskieDyktowanieTTS").start()

    def _tts_worker(self, text, settings):
        try:
            provider = settings.get("provider", settings.get("tts_provider", "gemini"))
            if provider == "grok":
                audio, mime = _xai_tts(settings.get("xai_key", ""), settings.get("xai_tts_voice"), text)
                _play_audio_bytes(audio, mime)
            elif provider == "openai":
                remaining = text.strip()
                while remaining:
                    if len(remaining) <= 4096:
                        part = remaining
                        remaining = ""
                    else:
                        cut = max(remaining.rfind(mark, 0, 4096) for mark in (". ", "! ", "? ", "\n", " "))
                        if cut < 1000:
                            cut = 4096
                        else:
                            cut += 1
                        part, remaining = remaining[:cut].strip(), remaining[cut:].strip()
                    audio, mime = _openai_tts(settings.get("openai_key", ""), settings.get("openai_tts_model"), settings.get("openai_tts_voice"), part)
                    _play_audio_bytes(audio, mime)
            else:
                audio, mime = _gemini_tts(settings.get("gemini_key", ""), settings.get("gemini_tts_model"), settings.get("gemini_tts_voice"), text)
                _play_audio_bytes(audio, mime)
            wx.CallAfter(ui.message, "Odtwarzanie mowy zakończone.")
        except Exception as e:
            self._write_log("TTS: {}".format(e))
            wx.CallAfter(ui.message, "Błąd syntezy mowy: {}".format(str(e)[:180]))
        finally:
            self._processing = False

    def _run_file_action(self, action, settings):
        if self._recording or self._processing:
            ui.message("Dodatek jest jeszcze zajęty.")
            return
        if not self._api_key(settings):
            ui.message("Najpierw ustaw klucz API wybranego dostawcy.")
            wx.CallAfter(self._show_settings)
            return
        self._processing = True
        wx.CallAfter(self._choose_file_for_action, action, settings)

    def _choose_file_for_action(self, action, settings):
        if action == "transcribe":
            message = "Wybierz nagranie audio lub wideo do transkrypcji"
            wildcard = (
                "Nagrania audio i wideo|*.wav;*.mp3;*.m4a;*.aac;*.flac;*.ogg;*.opus;*.webm;*.wma;*.aiff;*.aif;*.mp4;*.mov;*.mkv;*.avi;*.m4v;*.wmv;*.mpeg;*.mpg;*.3gp|"
                "Wszystkie pliki (*.*)|*.*"
            )
        else:
            message = "Wybierz plik tekstowy do zamiany na mowę"
            wildcard = "Pliki tekstowe (*.txt)|*.txt|Wszystkie pliki (*.*)|*.*"
        dialog = None
        popup_started = False
        file_path = None
        try:
            gui.mainFrame.prePopup()
            popup_started = True
            dialog = wx.FileDialog(gui.mainFrame, message=message, wildcard=wildcard, style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
            if dialog.ShowModal() == wx.ID_OK:
                file_path = dialog.GetPath()
        except Exception as e:
            self._write_log("Wybór pliku tekstu lub mowy: {}".format(e))
            ui.message("Nie udało się otworzyć okna wyboru pliku.")
        finally:
            if dialog:
                dialog.Destroy()
            if popup_started:
                gui.mainFrame.postPopup()
        if not file_path:
            self._processing = False
            return
        if action == "transcribe":
            ui.message("Przygotowuję nagranie do transkrypcji.")
            threading.Thread(target=self._transcribe_file_worker, args=(file_path, settings), daemon=True, name="PolskieDyktowanieFileSTT").start()
        else:
            ui.message("Odczytuję plik tekstowy i przygotowuję mowę.")
            threading.Thread(target=self._synthesize_file_worker, args=(file_path, settings), daemon=True, name="PolskieDyktowanieFileTTS").start()

    def _transcribe_file_worker(self, file_path, settings):
        try:
            with tempfile.TemporaryDirectory(prefix="polskieDyktowanie_stt_") as directory:
                segments = _extract_audio_segments(file_path, directory)
                transcripts = []
                for index, segment_path in enumerate(segments, 1):
                    wx.CallAfter(ui.message, "Rozpoznaję część {} z {}.".format(index, len(segments)))
                    with open(segment_path, "rb") as segment_file:
                        transcripts.append(self._transcribe(segment_file.read(), settings))
            transcript = "\n".join(item.strip() for item in transcripts if item.strip())
            result = self._process_text("dictation", transcript, settings)
            if not result:
                raise RuntimeError("Nie udało się rozpoznać tekstu z wybranego pliku.")
            self._last_result = result
            wx.CallAfter(self._save_description_as, "transcription", result)
        except Exception as e:
            self._write_log("Transkrypcja pliku: {}".format(e))
            wx.CallAfter(self._file_action_failed, "Nie udało się rozpoznać nagrania: {}".format(str(e)[:180]))

    def _synthesize_file_worker(self, file_path, settings):
        try:
            if os.path.getsize(file_path) > 5 * 1024 * 1024:
                raise RuntimeError("Plik tekstowy jest większy niż 5 MB.")
            raw = Path(file_path).read_bytes()
            text = None
            for encoding in ("utf-8-sig", "utf-16", "cp1250"):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeError:
                    continue
            if text is None:
                raise RuntimeError("Nie można odczytać kodowania pliku. Zapisz go jako TXT UTF-8.")
            text = text.strip()
            if not text:
                raise RuntimeError("Wybrany plik tekstowy jest pusty.")
            wav_data = self._create_speech_wav(text, settings)
            wx.CallAfter(self._save_audio_as, wav_data)
        except Exception as e:
            self._write_log("Synteza z pliku tekstowego: {}".format(e))
            wx.CallAfter(self._file_action_failed, "Nie udało się przygotować mowy: {}".format(str(e)[:180]))

    def _file_action_failed(self, message):
        self._processing = False
        ui.message(message)

    @staticmethod
    def _split_tts_text(text, max_chars=3500):
        remaining = str(text).strip()
        while remaining:
            if len(remaining) <= max_chars:
                yield remaining
                break
            cut = max(remaining.rfind(mark, 0, max_chars) for mark in (". ", "! ", "? ", "\n", " "))
            if cut < max_chars // 3:
                cut = max_chars
            else:
                cut += 1
            part, remaining = remaining[:cut].strip(), remaining[cut:].strip()
            if part:
                yield part

    def _create_speech_wav(self, text, settings):
        provider = settings.get("provider", "gemini")
        temp_root = tempfile.mkdtemp(prefix="polskieDyktowanie_tts_")
        try:
            wav_parts = []
            for index, part in enumerate(self._split_tts_text(text), 1):
                wx.CallAfter(ui.message, "Tworzę część mowy {}.".format(index))
                if provider == "grok":
                    audio, mime = _xai_tts(settings.get("xai_key", ""), settings.get("xai_tts_voice"), part)
                elif provider == "openai":
                    audio, mime = _openai_tts(settings.get("openai_key", ""), settings.get("openai_tts_model"), settings.get("openai_tts_voice"), part)
                else:
                    audio, mime = _gemini_tts(settings.get("gemini_key", ""), settings.get("gemini_tts_model"), settings.get("gemini_tts_voice"), part)
                source_suffix = ".wav" if "wav" in str(mime).lower() else ".mp3"
                source_path = os.path.join(temp_root, "source_{:03d}{}".format(index, source_suffix))
                if source_suffix == ".wav" and not audio.startswith(b"RIFF"):
                    audio = self._wrap_raw_pcm_as_wav(audio)
                with open(source_path, "wb") as audio_file:
                    audio_file.write(audio)
                normalized_path = os.path.join(temp_root, "part_{:03d}.wav".format(index))
                ffmpeg, _ffprobe = _find_ffmpeg_tools()
                try:
                    subprocess.run(
                        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", source_path,
                         "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", normalized_path],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, check=True,
                    )
                except subprocess.CalledProcessError as e:
                    detail = e.stderr.decode("utf-8", "replace")[-250:].strip() if e.stderr else ""
                    raise RuntimeError("Nie można przygotować dźwięku WAV. {}".format(detail))
                wav_parts.append(normalized_path)
            if not wav_parts:
                raise RuntimeError("Nie otrzymano dźwięku z silnika syntezy.")
            combined_path = os.path.join(temp_root, "speech.wav")
            with wave.open(combined_path, "wb") as combined:
                combined.setnchannels(1)
                combined.setsampwidth(2)
                combined.setframerate(24000)
                for wav_path in wav_parts:
                    with wave.open(wav_path, "rb") as part_file:
                        combined.writeframes(part_file.readframes(part_file.getnframes()))
            return Path(combined_path).read_bytes()
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    @staticmethod
    def _wrap_raw_pcm_as_wav(audio):
        return b"RIFF" + struct.pack("<I", 36 + len(audio)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16) + b"data" + struct.pack("<I", len(audio)) + audio

    def _save_audio_as(self, audio):
        dialog = None
        popup_started = False
        try:
            gui.mainFrame.prePopup()
            popup_started = True
            documents = wx.StandardPaths.Get().GetDocumentsDir()
            if not documents or not os.path.isdir(documents):
                documents = os.path.expanduser("~")
            dialog = wx.FileDialog(
                gui.mainFrame, message="Zapisz mowę jako plik WAV", defaultDir=documents,
                defaultFile="Synteza mowy.wav", wildcard="Plik audio WAV (*.wav)|*.wav",
                style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            )
            if dialog.ShowModal() != wx.ID_OK:
                ui.message("Anulowano zapisywanie nagrania.")
                return
            path = dialog.GetPath()
            if not path.lower().endswith(".wav"):
                path += ".wav"
            with open(path, "wb") as output:
                output.write(audio)
            ui.message("Nagranie zapisano jako plik WAV.")
        except Exception as e:
            self._write_log("Zapisywanie syntezy WAV: {}".format(e))
            ui.message("Nie udało się zapisać nagrania: {}".format(str(e)[:150]))
        finally:
            if dialog:
                dialog.Destroy()
            if popup_started:
                gui.mainFrame.postPopup()
            self._processing = False

    def _run_selected_mode(self):
        if self._recording or self._processing:
            ui.message("Dodatek już przetwarza zadanie.")
            return
        focus = api.getFocusObject()
        settings = dict(self._settings)
        if settings.get("visual_mode") in ("photo_file", "video_file"):
            self._run_visual_mode()
            return
        mode = settings.get("mode", "dictation")
        if mode in ("dictation", "chat", "translate"):
            if _is_editable(focus):
                self._start(focus)
            else:
                ui.message("Ustaw fokus w polu edycji, aby nagrać wypowiedź.")
        elif mode == "tts":
            self._speak_focus(focus, settings)
    def _run_visual_mode(self, requested_mode=None):
        settings = dict(self._settings)
        mode = requested_mode or settings.get("visual_mode", "screen")
        if mode not in _SettingsDialog.VISUAL_MODE_IDS:
            mode = "screen"
        if not self._api_key(settings):
            ui.message("Ustaw klucz API dostawcy, aby analizować obraz lub wideo.")
            wx.CallAfter(self._show_settings)
            return
        if mode in ("photo_file", "video_file"):
            self._processing = True
            ui.message("Wybierz plik do opisu.")
            wx.CallAfter(self._choose_visual_attachment, mode, settings)
            return
        self._start_visual_analysis(mode, settings)

    def _choose_visual_attachment(self, mode, settings):
        if mode == "photo_file":
            message = "Wybierz obraz do opisu"
            wildcard = "Pliki obrazów (*.png;*.jpg;*.jpeg;*.webp)|*.png;*.jpg;*.jpeg;*.webp"
        else:
            message = "Wybierz wideo do opisu"
            wildcard = "Pliki wideo (*.mp4;*.mov;*.mkv;*.avi;*.webm;*.m4v;*.wmv)|*.mp4;*.mov;*.mkv;*.avi;*.webm;*.m4v;*.wmv"
        dialog = None
        popup_started = False
        file_path = None
        try:
            gui.mainFrame.prePopup()
            popup_started = True
            dialog = wx.FileDialog(
                gui.mainFrame,
                message=message,
                wildcard=wildcard,
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
            )
            if dialog.ShowModal() == wx.ID_OK:
                file_path = dialog.GetPath()
        except Exception as e:
            self._write_log("Wybór załącznika: {}".format(e))
            wx.CallAfter(ui.message, "Nie udało się otworzyć okna wyboru pliku.")
        finally:
            if dialog:
                dialog.Destroy()
            if popup_started:
                gui.mainFrame.postPopup()
        if not file_path:
            self._processing = False
            return
        settings["visual_mode"] = mode
        self._start_visual_analysis(mode, settings, file_path)

    def _start_visual_analysis(self, mode, settings, file_path=None):
        focus = api.getFocusObject()
        focus_rect = None
        if mode == "photo":
            try:
                focus_rect = _focused_image_region(focus)
            except Exception as e:
                ui.message(str(e))
                self._processing = False
                return
        target = _focus_identity(focus) if _is_editable(focus) else None
        self._processing = True
        threading.Thread(target=self._visual_worker, args=(mode, settings, target, focus_rect, file_path), daemon=True, name="PolskieDyktowanieVision").start()

    def _visual_worker(self, mode, settings, target, focus_rect=None, file_path=None):
        try:
            task_names = {"photo": "obraz z fokusu NVDA", "photo_file": "załączony obraz", "screen": "ekran", "colors": "kolory na ekranie", "video": "wideo z ekranu", "video_file": "załączone wideo"}
            duration = " przez {} sekund".format(settings.get("video_seconds", 8)) if mode == "video" else ""
            wx.CallAfter(ui.message, "Analizuję {}{}.".format(task_names.get(mode, "obraz"), duration))
            result = self._run_visual(mode, settings, focus_rect, file_path)
            self._last_result = result
            wx.CallAfter(self._save_description_as, mode, result)
        except Exception as e:
            self._write_log("Analiza obrazu: {}".format(e))
            wx.CallAfter(ui.message, "Błąd analizy: {}".format(str(e)[:180]))
        finally:
            self._processing = False

    def _save_description_as(self, mode, text):
        text = str(text or "").strip()
        if not text:
            ui.message("Dostawca AI nie zwrócił opisu.")
            if mode == "transcription":
                self._processing = False
            return
        titles = {
            "photo_file": ("Opis obrazu z pliku", "Opis obrazu z pliku.txt"),
            "photo": ("Opis obrazu z fokusu NVDA", "Opis obrazu z fokusu NVDA.txt"),
            "screen": ("Opis ekranu", "Opis ekranu.txt"),
            "colors": ("Rozpoznane kolory", "Rozpoznane kolory.txt"),
            "video_file": ("Opis wideo z pliku", "Opis wideo z pliku.txt"),
            "video": ("Opis wideo z ekranu", "Opis wideo z ekranu.txt"),
            "transcription": ("Transkrypcja nagrania", "Transkrypcja nagrania.txt"),
        }
        title, default_file = titles.get(mode, ("Opis", "Opis.txt"))
        dialog = None
        popup_started = False
        try:
            gui.mainFrame.prePopup()
            popup_started = True
            documents = wx.StandardPaths.Get().GetDocumentsDir()
            if not documents or not os.path.isdir(documents):
                documents = os.path.expanduser("~")
            dialog = wx.FileDialog(
                gui.mainFrame,
                message="Zapisz transkrypcję jako" if mode == "transcription" else "Zapisz opis jako",
                defaultDir=documents,
                defaultFile=default_file,
                wildcard="Plik tekstowy (*.txt)|*.txt",
                style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            )
            if dialog.ShowModal() != wx.ID_OK:
                ui.message("Anulowano zapisywanie opisu.")
                return
            path = dialog.GetPath()
            if not path.lower().endswith(".txt"):
                path += ".txt"
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            with open(path, "w", encoding="utf-8-sig", newline="") as output:
                output.write(title + "\r\n\r\n")
                output.write(normalized.replace("\n", "\r\n"))
                output.write("\r\n")
            ui.message("Transkrypcję zapisano w pliku tekstowym." if mode == "transcription" else "Opis zapisano w pliku tekstowym.")
        except Exception as e:
            self._write_log("Zapisywanie opisu: {}".format(e))
            ui.message("Nie udało się zapisać opisu: {}".format(str(e)[:150]))
        finally:
            if dialog:
                dialog.Destroy()
            if popup_started:
                gui.mainFrame.postPopup()
            if mode == "transcription":
                self._processing = False

    def _copy_last_result(self):
        if self._last_result:
            api.copyToClip(self._last_result)
            ui.message("Ostatni wynik skopiowano do schowka.")
        else:
            ui.message("Brak ostatniego wyniku.")

    def _read_last_result(self):
        ui.message(self._last_result if self._last_result else "Brak ostatniego wyniku.")

    def _clear_log(self):
        try:
            with open(os.path.join(self._config_dir(), "polskieDyktowanie.log"), "w", encoding="utf-8") as file:
                file.write("")
            ui.message("Log wyczyszczono.")
        except Exception as e:
            ui.message("Nie udało się wyczyścić logu: {}".format(str(e)[:120]))

    def _play_cue(self, frequency, duration):
        try:
            tones.beep(int(frequency), int(duration))
        except Exception as e:
            self._write_log("Dźwięk statusu: {}".format(e))

    def _test_api(self):
        settings = dict(self._settings)
        if not self._api_key(settings):
            ui.message("Brak klucza API dla wybranego dostawcy.")
            return
        def worker():
            try:
                if settings.get("provider") == "grok":
                    _xai_chat(self._api_key(settings), self._model(settings), "Odpowiedz jednym słowem: OK")
                elif settings.get("provider") == "openai":
                    _openai_response(self._api_key(settings), self._model(settings), "Odpowiedz jednym słowem: OK")
                else:
                    _gemini_request(self._api_key(settings), self._model(settings), [{"text": "Odpowiedz jednym słowem: OK"}])
                wx.CallAfter(ui.message, "Połączenie z API działa.")
            except Exception as e:
                self._write_log("Test API: {}".format(e))
                wx.CallAfter(ui.message, "Test API nie powiódł się: {}".format(str(e)[:180]))
        threading.Thread(target=worker, daemon=True, name="PolskieDyktowanieTestAPI").start()

    def _fetch_models(self, provider=None):
        settings = dict(self._settings)
        model_list_type = provider or settings.get("provider")
        api_provider = model_list_type
        if model_list_type in ("gemini_tts", "vision_gemini"):
            api_provider = "gemini"
        elif model_list_type in ("openai_text", "openai_tts", "vision_openai"):
            api_provider = "openai"
        elif model_list_type == "vision_grok":
            api_provider = "grok"
        if api_provider in _SettingsDialog.PROVIDER_IDS:
            settings["provider"] = api_provider
        if not self._api_key(settings):
            ui.message("Najpierw zapisz klucz API wybranego dostawcy.")
            return
        def worker():
            try:
                if settings.get("provider") == "grok":
                    raw, _ = _http_json("https://api.x.ai/v1/models", headers={"Authorization": "Bearer " + self._api_key(settings)})
                    payload = json.loads(raw.decode("utf-8"))
                    models = sorted({str(x.get("id")) for x in payload.get("data", []) if x.get("id") and "grok" in str(x.get("id")).lower()})
                elif settings.get("provider") == "openai":
                    raw, _ = _http_json("https://api.openai.com/v1/models", headers={"Authorization": "Bearer " + self._api_key(settings)})
                    payload = json.loads(raw.decode("utf-8"))
                    if model_list_type == "openai_tts":
                        models = sorted({
                            str(item.get("id")) for item in payload.get("data", [])
                            if "tts" in str(item.get("id", "")).lower()
                        })
                    else:
                        excluded = ("image", "audio", "transcri", "realtime", "embedding", "moderation", "tts", "live", "codex")
                        models = sorted({
                            str(item.get("id")) for item in payload.get("data", [])
                            if str(item.get("id", "")).startswith("gpt-")
                            and not any(token in str(item.get("id", "")).lower() for token in excluded)
                        })
                else:
                    url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000&key=" + urllib.parse.quote(self._api_key(settings), safe="")
                    raw, _ = _http_json(url)
                    payload = json.loads(raw.decode("utf-8"))
                    models = []
                    for item in payload.get("models", []):
                        name = str(item.get("name", "")).removeprefix("models/")
                        lowered = name.lower()
                        methods = item.get("supportedGenerationMethods", [])
                        if model_list_type == "gemini_tts":
                            is_tts_model = "tts" in lowered and "embedding" not in lowered
                            supports_generation = "generateContent" in methods or not methods
                            if name.startswith("gemini-") and is_tts_model and supports_generation:
                                models.append(name)
                        elif name.startswith("gemini-") and "embedding" not in lowered and "image" not in lowered and "tts" not in lowered and "generateContent" in methods:
                            models.append(name)
                    models = sorted(set(models))
                wx.CallAfter(self._show_model_choices, models, model_list_type)
            except Exception as e:
                self._write_log("Lista modeli: {}".format(e))
                wx.CallAfter(ui.message, "Nie udało się pobrać modeli: {}".format(str(e)[:180]))
        threading.Thread(target=worker, daemon=True, name="PolskieDyktowanieModels").start()

    def _show_model_choices(self, models, provider):
        if not models:
            ui.message("API nie zwróciło listy modeli.")
            return
        if self._settings_dialog is not None:
            self._settings_dialog.set_model_choices(models, provider)
            return
        dialog = wx.SingleChoiceDialog(gui.mainFrame, "Wybierz model do zapisania", "Dostępne modele", models)
        try:
            if dialog.ShowModal() == wx.ID_OK:
                model_keys = {
                    "gemini_tts": "gemini_tts_model",
                    "openai_tts": "openai_tts_model",
                    "grok": "grok_model",
                    "openai": "openai_text_model",
                    "openai_text": "openai_text_model",
                    "vision_gemini": "gemini_vision_model",
                    "vision_grok": "grok_vision_model",
                    "vision_openai": "openai_vision_model",
                }
                key = model_keys.get(provider, "model")
                self._save_settings({key: models[dialog.GetSelection()]})
        finally:
            dialog.Destroy()

    def _insert_result(self, text, target_hwnd, read_result, settings=None):
        text = (text or "").strip()
        if not text:
            ui.message("Dostawca AI nie zwrócił tekstu.")
            return
        settings = settings or self._settings
        current = api.getFocusObject()
        if _is_editable(current) and _focus_identity(current) == target_hwnd:
            try:
                separator = {"space": " ", "newline": "\n", "none": ""}.get(settings.get("separator", "space"), " ")
                existing = getattr(current, "value", "") or ""
                if existing and not str(existing).endswith((" ", "\n", "\t")):
                    text = separator + text
                api.copyToClip(text)
                keyboardHandler.KeyboardInputGesture.fromName("control+v").send()
                if settings.get("auto_send"):
                    keyboardHandler.KeyboardInputGesture.fromName("enter").send()
                ui.message(text if read_result else "Wstawiono tekst.")
                return
            except Exception as e:
                self._write_log("Wstawianie tekstu: {}".format(e))
        try:
            api.copyToClip(text)
            ui.message("Fokus zmienił się. Wynik skopiowano do schowka.")
        except Exception as e:
            self._write_log("Kopiowanie wyniku: {}".format(e))
            ui.message("Nie udało się wstawić ani skopiować tekstu.")

    @script(description="Rozpocznij lub zakończ tryb w polu edycji; poza polem otwórz ustawienia.")
    def script_dictationOrSettings(self, gesture):
        if self._recording:
            if self._stop_event:
                self._stop_event.set()
                self._play_cue(500, 120)
                ui.message("Kończę nagranie.")
            return
        if self._processing:
            ui.message("Dyktowanie jest jeszcze przetwarzane.")
            return
        focus = api.getFocusObject()
        if _is_editable(focus):
            if self._settings.get("mode") in ("dictation", "chat", "translate"):
                self._start(focus)
            elif self._settings.get("mode") == "tts":
                self._speak_focus(focus, self._settings)
            else:
                ui.message("Wybierz tryb tekstowy w ustawieniach. Skrót z Shift uruchamia analizę obrazu lub wideo.")
        else:
            wx.CallAfter(self._show_settings)

    @script(description="Rozpocznij lub zakończ bezpośrednie dyktowanie mowy na tekst.")
    def script_runSpeechToText(self, gesture):
        if self._recording:
            if self._stop_event:
                self._stop_event.set()
                self._play_cue(500, 120)
                ui.message("Kończę nagranie.")
            return
        if self._processing:
            ui.message("Dodatek jest jeszcze zajęty.")
            return
        focus = api.getFocusObject()
        if not _is_editable(focus):
            ui.message("Aby dyktować, ustaw fokus w polu edycji.")
            return
        self._start(focus, "dictation")

    @script(description="Odczytaj zaznaczony lub dostępny tekst za pomocą syntezy mowy.")
    def script_runTextToSpeech(self, gesture):
        if self._recording or self._processing:
            ui.message("Dodatek jest jeszcze zajęty.")
            return
        focus = api.getFocusObject()
        self._speak_focus(focus, dict(self._settings))

    @script(description="Wybierz plik audio lub wideo i zapisz jego transkrypcję jako tekst.")
    def script_runFileTranscription(self, gesture):
        self._run_file_action("transcribe", dict(self._settings))

    @script(description="Wybierz plik tekstowy i zapisz syntezę mowy jako plik WAV.")
    def script_runFileSynthesis(self, gesture):
        self._run_file_action("synthesize_file", dict(self._settings))

    @script(description="Uruchom wybraną analizę obrazu, kolorów lub wideo niezależnie od fokusu.")
    def script_runVisualMode(self, gesture):
        if self._recording:
            ui.message("Najpierw zakończ bieżące nagranie głównym skrótem.")
            return
        if self._processing:
            ui.message("Dodatek jest jeszcze zajęty.")
            return
        self._run_visual_mode()

    def _run_direct_visual_mode(self, mode):
        if self._recording:
            ui.message("Najpierw zakończ bieżące nagranie głównym skrótem.")
            return
        if self._processing:
            ui.message("Dodatek jest jeszcze zajęty.")
            return
        self._run_visual_mode(mode)

    @script(description="Opisz wybrany plik obrazu.")
    def script_runVisual_photo_file(self, gesture):
        self._run_direct_visual_mode("photo_file")

    @script(description="Opisz obraz znajdujący się pod fokusem NVDA.")
    def script_runVisual_photo(self, gesture):
        self._run_direct_visual_mode("photo")

    @script(description="Opisz cały ekran.")
    def script_runVisual_screen(self, gesture):
        self._run_direct_visual_mode("screen")

    @script(description="Rozpoznaj kolory na ekranie.")
    def script_runVisual_colors(self, gesture):
        self._run_direct_visual_mode("colors")

    @script(description="Opisz wybrany plik wideo.")
    def script_runVisual_video_file(self, gesture):
        self._run_direct_visual_mode("video_file")

    @script(description="Opisz wideo z ekranu.")
    def script_runVisual_video(self, gesture):
        self._run_direct_visual_mode("video")


