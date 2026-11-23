#!/usr/bin/env python3
"""Skanuje nowe filmy z TikToka w poszukiwaniu baneru i zapisuje wynik do data/banners.json.

Lista filmów (id, tytuł, wyświetlenia, data) pochodzi z data/tiktok.js na stronie
369shoter.github.io, którą i tak co 5 minut odświeża GitHub. Każdy film skanujemy tylko raz
(baner w filmie się nie zmienia): pobieramy go przez yt-dlp, ffmpeg wyciąga klatkę co
SCAN_EVERY_S sekund, a scripts/detect.py szuka na niej baneru.

Wpisy w data/banners.json:
  "<id>": {"banner": true/false, "at": <czas skanu>, "sec": <sekunda z banerem>, ...}
  "<id>": {"error": "...", "tries": N}  - nie udało się pobrać, spróbujemy przy następnym razie

Uruchomienie lokalnie:  python scripts/scan.py [--limit 10] [--id 123 --id 456] [--rescan]
"""
import argparse
import datetime
import glob
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import cv2

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from detect import find_banner  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
BANNERS = ROOT / "data" / "banners.json"
SOURCE = "https://369shoter.github.io/data/tiktok.js"
USER = "369_shoter"
SCAN_EVERY_S = 1          # klatka co sekundę (baner trwa ~4 s, więc łapiemy go kilka razy)
MAX_TRIES = 6             # po tylu nieudanych pobraniach przestajemy próbować (zostaje ręczne zaznaczenie)
TIME_BUDGET_S = 20 * 60   # jeden przebieg skanuje najwyżej tyle czasu, reszta przy następnym


def log(msg):
    print(msg, flush=True)


def warn(msg):
    print(("::warning::" if os.environ.get("GITHUB_ACTIONS") else "UWAGA: ") + msg, flush=True)


def load_videos():
    req = urllib.request.Request(SOURCE + "?t=%d" % time.time(), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    return data["videos"]


def load_banners():
    if BANNERS.exists():
        return json.loads(BANNERS.read_text(encoding="utf-8"))
    return {}


def save_banners(banners):
    BANNERS.parent.mkdir(parents=True, exist_ok=True)
    ordered = dict(sorted(banners.items(), key=lambda kv: kv[0], reverse=True))
    BANNERS.write_text(json.dumps(ordered, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def download(video_id, folder):
    out = os.path.join(folder, "v.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "--no-playlist",
        "-f", "best[height<=1080]/best", "-o", out,
        f"https://www.tiktok.com/@{USER}/video/{video_id}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    files = glob.glob(os.path.join(folder, "v.*"))
    if res.returncode != 0 or not files:
        msg = (res.stderr or res.stdout or "brak pliku").strip().splitlines()
        raise RuntimeError(msg[-1][:200] if msg else "nie udało się pobrać")
    return files[0]


def scan_file(path, folder):
    frames = os.path.join(folder, "f")
    os.makedirs(frames, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", path, "-vf", f"fps=1/{SCAN_EVERY_S},scale=360:-2",
         os.path.join(frames, "%04d.png")],
        check=True, timeout=300,
    )
    names = sorted(glob.glob(os.path.join(frames, "*.png")))
    hits = []
    for i, name in enumerate(names):
        img = cv2.imread(name)
        if img is None:
            continue
        r = find_banner(img)
        if r:
            hits.append((i * SCAN_EVERY_S, r))
    return len(names), hits


def scan_video(video_id):
    folder = tempfile.mkdtemp(prefix="scan_")
    try:
        path = download(video_id, folder)
        total, hits = scan_file(path, folder)
        entry = {"banner": bool(hits), "at": int(time.time()), "frames": total}
        if hits:
            entry["sec"] = hits[0][0]
            entry["hits"] = len(hits)
            entry["color"] = hits[0][1]["color"]
        return entry
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="najwyżej tyle filmów w tym przebiegu")
    ap.add_argument("--id", action="append", default=[], help="skanuj tylko te filmy")
    ap.add_argument("--rescan", action="store_true", help="skanuj ponownie także już sprawdzone")
    ap.add_argument("--budget", type=int, default=TIME_BUDGET_S, help="limit czasu przebiegu w sekundach")
    args = ap.parse_args()

    try:
        videos = load_videos()
    except Exception as exc:  # noqa: BLE001
        warn(f"Nie udało się pobrać listy filmów: {exc}")
        return 0
    banners = load_banners()

    # zapamiętujemy też tytuł, datę i ostatnie wyświetlenia, żeby film nie zniknął z panelu,
    # gdy wypadnie z listy na stronie
    for v in videos:
        e = banners.get(v["id"])
        if e is not None:
            e["title"] = v.get("title", "")
            if v.get("t"):
                e["t"] = v["t"]
            if v.get("views") is not None:
                e["views"] = v["views"]

    todo = []
    for v in videos:
        e = banners.get(v["id"])
        if args.id and v["id"] not in args.id:
            continue
        if e is None or args.rescan or args.id:
            todo.append(v)
        elif "banner" not in e and e.get("tries", 0) < MAX_TRIES:
            todo.append(v)
    todo.sort(key=lambda v: v["id"], reverse=True)  # najnowsze najpierw
    if args.limit:
        todo = todo[:args.limit]
    log(f"Filmów na liście: {len(videos)}, do sprawdzenia: {len(todo)}")

    started = time.time()
    done = 0
    for v in todo:
        if time.time() - started > args.budget:
            log("Koniec czasu na ten przebieg, reszta następnym razem.")
            break
        vid = v["id"]
        base = {"title": v.get("title", ""), "views": v.get("views"), "t": v.get("t")}
        try:
            entry = scan_video(vid)
            entry.update(base)
            banners[vid] = entry
            log(f"{vid}  {'BANER' if entry['banner'] else 'brak'}  ({entry['frames']} klatek)  {base['title'][:50]}")
        except Exception as exc:  # noqa: BLE001
            prev = banners.get(vid, {})
            tries = prev.get("tries", 0) + 1
            banners[vid] = {**base, "error": str(exc)[:200], "tries": tries}
            warn(f"{vid}: {exc} (próba {tries})")
        done += 1
        if done % 5 == 0:
            save_banners(banners)
    save_banners(banners)
    log(f"Gotowe: sprawdzono {done}. Z banerem łącznie: {sum(1 for e in banners.values() if e.get('banner'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
