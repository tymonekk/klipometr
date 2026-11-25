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

Na stronie, w sekcji „Wysyłka do StoreMedia”, wklejasz link do klipu (też skrócony z telefonu) i klikasz „Dodaj klip”.
Strona wysyła link do Workera Cloudflare (`worker/`), który idzie za przekierowaniami TikToka do numeru filmu,
sprawdza, że to film z @369_shoter, i zapisuje go w repo jako `data/storemedia/<numer>.json`.
Bot (`scripts/remind.py`, krok w `scan.yml`) liczy czas wrzucenia (numer filmu >> 32, czas polski) i 6 dni później
wysyła jedno przypomnienie na Telegram (zapis w `data/reminders.json`). Między 22:00 a 8:00 czeka do rana.
„usuń” przy klipie (zakładka „StoreMedia” w klipach): na urządzeniu, które dodało klip, od razu; na innych
strona raz prosi o kod usuwania (sekret Workera `DELETE_PIN`, zmiana: `npx wrangler secret put DELETE_PIN` w `worker/`).

Sekrety repo: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Test: Actions → „Test Telegrama” → Run workflow.

### Worker (jednorazowo)

1. Token GitHuba: Settings → Developer settings → Fine-grained tokens → tylko repo `klipometr`,
   uprawnienie Contents: Read and write.
2. W folderze `worker/`:
   ```
   npx wrangler login
   npx wrangler deploy
   npx wrangler secret put GITHUB_TOKEN
   ```
3. Adres z `wrangler deploy` (`https://klipometr-storemedia.<konto>.workers.dev`) wpisz w `SM_API` w `index.html`.

## Lokalnie

```
pip install yt-dlp opencv-python numpy
python scripts/scan.py --limit 5        # sprawdź 5 nowych filmów
python scripts/scan.py --id 7708908718565510433 --rescan
python scripts/remind.py --dry-run      # jakie przypomnienia by poszły
python scripts/remind.py --build data/storemedia.json   # lista StoreMedia dla panelu lokalnie
python -m http.server 8765              # panel na http://localhost:8765
```
