POLSKIE DYKTOWANIE DLA NVDA — 2.9.3

Dodatek przenosi do NVDA główne funkcje Polskiego Dyktanda 1.3.15.
Autor Polskiego Dyktanda: Madridista/Realista. Informacja o autorze jest także
dostępna w ustawieniach dodatku, na karcie „Ogólne”.

SKRÓTY
Alt+Q:
  - W polu edycji: uruchamia wybrany tryb. Dla dyktowania, czatu i
    tłumaczenia pierwsze naciśnięcie rozpoczyna nagrywanie, a drugie kończy.
  - Poza polem edycji: otwiera ustawienia.
Alt+Shift+Q (lub wybrany skrót analizy) uruchamia tryb wybrany na karcie
„Obraz”. Dodatkowo dostępne są skróty bezpośrednie dla każdego rodzaju opisu:
Ctrl+Alt+Shift+F1 — opis obrazu z pliku; F2 — opis obrazu pod fokusem NVDA;
F3 — opis całego ekranu; F4 — rozpoznawanie kolorów; F5 — opis wideo z pliku;
F6 — opis wideo z ekranu. Dla F1 i F5 dodatek otwiera wybór właściwego pliku,
jeśli nie ma zapamiętanego załącznika tego typu. Skróty bezpośrednie można
zmienić w zakładce „Skróty”. Skróty można też zmienić w NVDA > Ustawienia >
Zdarzenia wejściowe. Tryby ekranu analizują pulpit.

TRYBY
- Dyktowanie z korektą, interpunkcją, obsługą form żeńskich/męskich, nazw,
  akapitów, emoji w serdecznych/emocjonalnych wypowiedziach, wulgaryzmów
  i własnym słownikiem zamian.
- Inteligentny czat z historią do sześciu ostatnich wymian.
- Tłumaczenie wypowiedzi na wybrany język.
- Synteza mowy zaznaczonego lub dostępnego tekstu przez aktywny silnik Gemini, Grok/xAI lub OpenAI API.
- Opis obrazu z pliku lub fokusu NVDA, obrazu całego ekranu, kolorów na ekranie,
  klatek z załączonego pliku wideo albo wideo z ekranu.
- Po zakończeniu każdego opisu otwiera się okno „Zapisz jako”. Wybierz folder
  i nazwę pliku TXT. Opis jest zapisany jako czytelny tekst UTF-8 z polskimi
  znakami; nie jest automatycznie kopiowany do schowka.
- Obsługa Gemini, Grok/xAI i OpenAI API. Można przetestować API i pobrać listę modeli.
- OpenAI API obsługuje transkrypcję dyktowania oraz analizę obrazu, ekranu i wideo.
- Modele tekstowe i wizualne wybiera się osobno. Model do opisu i przycisk
  pobierania na karcie „Obraz” zmieniają się wraz z aktywnym dostawcą.
  Transkrypcja audio OpenAI korzysta ze stałego modelu gpt-transcribe.
- Opcjonalne uruchomienie Windows Voice Typing (Win+H) w trybie dyktowania.
- Kopiowanie i odczyt ostatniego wyniku, separator wstawianego tekstu oraz
  opcjonalne naciśnięcie Enter po wstawieniu.
- Sygnały dźwiękowe przy otwarciu ustawień, rozpoczęciu i zakończeniu nagrywania.

KONFIGURACJA
W ustawieniach dodatku na karcie „Ogólne” wybierz dostawcę przyciskiem opcji
„Gemini / Google”, „Grok / xAI” lub „OpenAI API”. Pole „Klucz API” poniżej
zawsze dotyczy wybranego dostawcy. Wpisany klucz zapisuje się po zatwierdzeniu
ustawień. Klucze pozostałych dostawców pozostają zapisane oddzielnie. Wybierz
też tryb tekstowy. Dostawca ten obsługuje dyktowanie, czat, tłumaczenie,
obraz i wideo oraz zapisuje się od razu. Syntezę mowy konfiguruje się osobno.
Model tekstowy wybiera się na karcie dostawcy. Model do obrazu, ekranu, kolorów i wideo
oraz przycisk pobierania znajdują się na karcie „Obraz” i zmieniają się
wraz z aktywnym dostawcą z karty „Ogólne”. Transkrypcja audio OpenAI używa
modelu gpt-transcribe.
Wideo z pliku wymaga programów FFmpeg i FFprobe dostępnych w systemowym PATH.
Karta „Synteza mowy” automatycznie używa dostawcy wybranego na karcie „Ogólne”
i jego klucza API. Zmiana dostawcy przełącza także ustawienia syntezy. Model
i głos wybiera się na karcie „Synteza mowy”; listę modeli Gemini lub OpenAI TTS
można pobrać z tej karty. xAI samo dobiera model TTS i udostępnia wybór głosu.
OpenAI API korzysta z modelu gpt-4o-mini-tts (dłuższy tekst jest dzielony na
fragmenty po maksymalnie 4096 znaków); OpenAI zapowiada wycofanie tego modelu
6 stycznia 2027 r. Zapisany klucz jest pokazany jako zamaskowany; wpisanie
nowej wartości na karcie „Ogólne” go zmieni.
Klucze są chronione
Windows DPAPI dla bieżącego konta Windows.
Ustawienia zawierają także języki, modele, głosy TTS, limit klatek filmu
i szczegółowość. W zakładce „Tekst i języki” postępowanie z wulgaryzmami, ochronę
przed mową z tła oraz separator wstawiany przed nowym tekstem opisują nazwane
grupy opcji. Słownik zamian jest opcjonalny: wpisz jedną regułę na wiersz w
formacie błędna forma => poprawna forma. Służy do korekty nazw własnych i
terminów po rozpoznaniu mowy. Puste pole wyłącza słownik.

INSTALACJA
1. Otwórz Polskie_Dyktowanie_NVDA_2.9.3.nvda-addon.
2. Potwierdź instalację i uruchom ponownie NVDA.
3. Naciśnij Alt+Q poza polem edycji i skonfiguruj dostawcę oraz klucz API.
4. Wybierz typ analizy na karcie „Obraz”. Możesz uruchomić jego skrót bezpośredni
   albo użyć Alt+Shift+Q dla trybu wybranego w ustawieniach. Załącznik wybierz
   wcześniej w ustawieniach lub wskaż go po wywołaniu skrótu obrazu/wideo z pliku.
5. Do opisu filmu zainstaluj FFmpeg dla Windows i dodaj folder zawierający
   ffmpeg.exe oraz ffprobe.exe do PATH. Oficjalna strona: https://www.ffmpeg.org/download.html.

PRYWATNOŚĆ I OGRANICZENIA
- Nagranie mikrofonu jest przechowywane w pamięci, a następnie wysyłane przez
  HTTPS do wybranego dostawcy AI. Maksymalny czas to 5 minut.
- Wybrane pliki PNG, JPG i WebP (do 10 MB) są wysyłane do dostawcy AI. Dla pliku
  wideo dodatek wysyła wybrane klatki, a nie dźwięk. Wideo z ekranu i opis ekranu wysyłają
  zrzuty pulpitu; kolory wysyłają bieżący ekran. Zasłoń treści, których nie
  chcesz udostępniać dostawcy.
- TTS zapisuje wynik audio tymczasowo w pliku systemowym na czas odtwarzania,
  po czym usuwa plik.
- Jeśli fokus zmieni się podczas przetwarzania, tekst jest kopiowany zamiast
  wklejany. Enter po wklejeniu może wysłać formularz.
- Opisy wizualne są zapisywane tylko po wskazaniu miejsca w oknie „Zapisz jako”.
  Przycisk kopiowania ostatniego wyniku nadal pozwala ręcznie skopiować opis.
- Pola wyboru list modeli pokazują modele udostępnione przez API; nie każdy
  model musi obsługiwać każdy tryb.
- OpenAI API wymaga klucza z platformy API OpenAI. Rozliczenia API są osobne
  od subskrypcji ChatGPT.
- Automatyczny instalator aktualizacji z paczki Android PPK nie jest częścią
  dodatku NVDA. NVDA zarządza instalacją dodatków.

Minimalna wymagana wersja NVDA: 2024.1. Użytkownik potwierdził sprawdzenie
paczki 2.9.3 w NVDA 2026.1. Klucze API i użycie modeli podlegają warunkom
dostawcy.

Oryginalna paczka: Polskie Dyktando 1.3.15, autor Madridista/Realista.
Licencja dodatku i jego kodu: GNU GPL wersja 2 lub nowsza. Pełny tekst znajduje
się w pliku COPYING.txt.


