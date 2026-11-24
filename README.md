# Klipometr

Panel z zarobkiem z filmów TikTok, w których pojawia się baner (ruletka skrzynek).
Strona: https://tymonekk.github.io/licznik-klipow/

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

## Lokalnie

```
pip install yt-dlp opencv-python numpy
python scripts/scan.py --limit 5        # sprawdź 5 nowych filmów
python scripts/scan.py --id 7708908718565510433 --rescan
python -m http.server 8765              # panel na http://localhost:8765
```
