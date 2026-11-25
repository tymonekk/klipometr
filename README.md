# Klipometr

Panel z zarobkiem z filmów TikTok, w których pojawia się baner (ruletka skrzynek).
Strona: https://tymonekk.github.io/klipometr/

## Jak to działa

- **Wyświetlenia** panel pobiera na żywo z `https://369shoter.github.io/data/tiktok.js`
  (to samo, co odświeża strona główna co ~5 min).
- **Baner** wykrywa bot w GitHub Actions (`.github/workflows/scan.yml`, co 30 min):
  pobiera każdy nowy film (`yt-dlp`), wyciąga klatkę co sekundę (`ffmpeg`) i szuka na niej
  szerokiego prostokąta z niebieską albo złotą ramką (`scripts/detect.py`, OpenCV).
  Wynik trafia do `data/banners.json`; każdy film jest skanowany tylko raz.
- **Zarobek**: 0,70 $ za 1000 wyświetleń, tylko filmy z banerem i co najmniej 5000 wyświetleń.
  Kurs USD/PLN z API NBP.

## Bot się pomylił?

Wpisz id filmu (liczba z linku do filmu) w `data/overrides.json`:

```json
{ "baner": ["7708908718565510433"], "bez_baneru": [] }
```

## Wysyłka do StoreMedia i przypomnienia

Klip z banerem trzeba wysłać do StoreMedia (https://rushviral.com/tworca/wspolprace) do 6 dni od wrzucenia
(czas wrzucenia = numer filmu >> 32, czas polski). Bot (`scripts/remind.py`, krok w `scan.yml`) po terminie
wysyła jedno przypomnienie na Telegram i zapisuje to w `data/reminders.json`. Między 22:00 a 8:00 czeka do rana.

`data/storemedia.json` (edytujesz ręcznie):

```json
{ "od": "2026-11-19", "wyslane": ["7711453002296479009"], "dodatkowe": [] }
```

- `wyslane`: klipy już wysłane (koniec przypomnień, na stronie zielony status),
- `dodatkowe`: filmy z banerem, których bot nie widzi (ukryte, 18+),
- `od`: klipy wrzucone wcześniej są pomijane.

Sekrety repo: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Test: Actions → „Test Telegrama” → Run workflow.

## Lokalnie

```
pip install yt-dlp opencv-python numpy
python scripts/scan.py --limit 5        # sprawdź 5 nowych filmów
python scripts/scan.py --id 7708908718565510433 --rescan
python scripts/remind.py --dry-run      # jakie przypomnienia by poszły
python -m http.server 8765              # panel na http://localhost:8765
```
