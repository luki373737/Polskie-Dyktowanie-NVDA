# Changelog

## 2.9.9
- Added an accessible project support page before intentionally opening add-on settings, with PayPal and Revolut links.
- Dictation and description shortcuts do not show the support page, including when settings open automatically because an API key is missing.

## 2.9.6
- Added audio/video file transcription on the “Mowa na tekst” page, with the result saved through a Save As dialog as UTF-8 TXT.
- Added TXT-file speech synthesis on the “Tekst na mowę” page, with generated speech saved through a Save As dialog as WAV.
- File transcription extracts and transcribes audio in five-minute sections; video is transcribed from its audio track.

## 2.9.8
- Replaced Gemini and xAI TTS voice text fields with accessible voice selection lists.
- Tested with NVDA 2026.3.1.

## 2.9.5
- File description shortcuts always open a picker for a new image or video instead of reusing the previous file.
- Renamed settings pages to clearly separate speech-to-text and text-to-speech.
- Added direct configurable shortcuts for speech-to-text and text-to-speech (Ctrl+Alt+Shift+F7 and F8 by default).
- Bundled FFmpeg and FFprobe so video files can be described without installing separate codec tools. License and source information is included.

## 2.9.3
- Visual descriptions now open a Save As dialog and can be saved to readable UTF-8 TXT files with Polish characters.
- Added author attribution to the General settings page.
- Added English store metadata with Polish localization.

## 2.9.2
- Added direct keyboard shortcuts for image, screen, color, and video descriptions.
