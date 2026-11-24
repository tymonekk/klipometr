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
import re
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
VIEWS = ROOT / "data" / "views.json"
OVERRIDES = ROOT / "data" / "overrides.json"
HISTORY_GAP_S = 50 * 60   # nowy punkt historii wyświetleń najwyżej co ~godzinę
HISTORY_KEEP_DAYS = 45
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
SOURCE = "https://369shoter.github.io/data/tiktok.js"
LIVE = "https://369-panel.369shoter.workers.dev/live"
USER = "369_shoter"
SCAN_EVERY_S = 1          # klatka co sekundę (baner trwa ~4 s, więc łapiemy go kilka razy)
MAX_TRIES = 6             # tyle prób pod rząd, potem rzadziej (patrz should_retry)
RETRY_EVERY_S = 12 * 3600
RETRY_MAX_AGE_S = 30 * 86400
TIME_BUDGET_S = 20 * 60   # jeden przebieg skanuje najwyżej tyle czasu, reszta przy następnym


def log(msg):
    print(msg, flush=True)


def warn(msg):
    print(("::warning::" if os.environ.get("GITHUB_ACTIONS") else "UWAGA: ") + msg, flush=True)


def load_videos():
    """Lista filmów: pełna z data/tiktok.js strony + najświeższe z /live Workera strony (łapie nowe filmy szybciej)."""
    req = urllib.request.Request(SOURCE + "?t=%d" % time.time(), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    videos = json.loads(text[text.index("{"):text.rindex("}") + 1])["videos"]
    try:
        req = urllib.request.Request(LIVE, headers={"User-Agent": "Mozilla/5.0", "Origin": "https://369shoter.github.io"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            live = json.loads(resp.read().decode("utf-8")).get("videos") or []
        known = {v["id"]: v for v in videos}
        for lv in live:
            vid = str(lv.get("id", ""))
            if not vid.isdigit():
                continue
            if vid in known:
                known[vid]["views"] = max(known[vid].get("views") or 0, lv.get("views") or 0)
            else:
                # numer filmu TikToka zawiera czas wgrania (górne 32 bity)
                videos.append({"id": vid, "title": lv.get("title", ""), "views": lv.get("views") or 0, "t": int(vid) >> 32})
    except Exception as exc:  # noqa: BLE001
        warn(f"/live niedostępne: {exc}")
    return videos


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def page_views(video_id):
    """Świeża liczba wyświetleń ze strony filmu na TikToku (dokładna do ~100 przy dużych liczbach)."""
    req = urllib.request.Request(f"https://www.tiktok.com/@{USER}/video/{video_id}",
                                 headers={"User-Agent": UA, "Accept-Language": "pl,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", "replace")
    m = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("brak danych na stronie filmu")
    detail = json.loads(m.group(1))["__DEFAULT_SCOPE__"].get("webapp.video-detail") or {}
    item = (detail.get("itemInfo") or {}).get("itemStruct")
    if not item or not item.get("stats"):
        raise RuntimeError("film niewidoczny publicznie (%s)" % (detail.get("statusMsg") or detail.get("statusCode")))
    stats = item.get("statsV2") or item.get("stats") or {}
    return int(stats["playCount"]), int(item.get("createTime") or 0)


def refresh_views(banners):
    """Odświeża wyświetlenia filmów z banerem (także zaznaczonych ręcznie) i dopisuje historię."""
    over = read_json(OVERRIDES, {})
    ids = {k for k, e in banners.items() if e.get("banner")} | {str(i) for i in over.get("baner", [])}
    ids -= {str(i) for i in over.get("bez_baneru", [])}
    data = read_json(VIEWS, {"at": 0, "v": {}, "h": {}})
    now = int(time.time())
    ok = 0
    for vid in sorted(ids, reverse=True):
        try:
            views, created = page_views(vid)
        except Exception as exc:  # noqa: BLE001
            warn(f"wyświetlenia {vid}: {exc}")
            continue
        old = data["v"].get(vid, 0)
        views = max(views, old)                      # wyświetlenia nie maleją (TikTok bywa niespójny)
        data["v"][vid] = views
        hist = data["h"].setdefault(vid, [])
        if not hist or (now - hist[-1][0] >= HISTORY_GAP_S and views != hist[-1][1]):
            hist.append([now, views])
        if vid in banners:
            banners[vid]["views"] = views
            if created:   # prawdziwa godzina publikacji (zaplanowany post wychodzi później niż wgranie)
                banners[vid]["t"] = created
                banners[vid]["t_src"] = "page"
        ok += 1
        time.sleep(0.3)
    cutoff = now - HISTORY_KEEP_DAYS * 86400
    for vid, hist in data["h"].items():
        data["h"][vid] = [p for p in hist if p[0] >= cutoff] or hist[-1:]
    if ok:
        data["at"] = now
    VIEWS.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    log(f"Odświeżono wyświetlenia {ok} z {len(ids)} filmów z banerem")


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
    near = {}
    near_sec = None
    for i, name in enumerate(names):
        img = cv2.imread(name)
        if img is None:
            continue
        before = near.get("score", 0)
        r = find_banner(img, near)
        if near.get("score", 0) > before:
            near_sec = i * SCAN_EVERY_S
        if r:
            hits.append((i * SCAN_EVERY_S, r))
    return len(names), hits, (near.get("score"), near_sec)


def error_reason(msg):
    """Krótki powód, dla którego nie da się pobrać filmu (pokazywany w panelu)."""
    m = msg.lower()
    if "comfortable" in m or "log in for access" in m:
        return "18+"
    if "ip address is blocked" in m or "self_see" in m or "private" in m or "unavailable" in m:
        return "ukryty"
    return "blad"


def should_retry(e, v):
    """Najpierw MAX_TRIES prób pod rząd, potem raz na RETRY_EVERY_S, dopóki film ma mniej niż RETRY_MAX_AGE_S
    (ukryty film może wrócić po odwołaniu, a chwilowa blokada TikToka minąć)."""
    tries = e.get("tries", 0)
    if tries < MAX_TRIES:
        return True
    age = time.time() - (v.get("t") or e.get("t") or 0)
    return age < RETRY_MAX_AGE_S and time.time() - e.get("last_try", 0) > RETRY_EVERY_S


def scan_video(video_id):
    folder = tempfile.mkdtemp(prefix="scan_")
    try:
        path = download(video_id, folder)
        total, hits, (near, near_sec) = scan_file(path, folder)
        entry = {"banner": bool(hits), "at": int(time.time()), "frames": total}
        if hits:
            entry["sec"] = hits[0][0]
            entry["hits"] = len(hits)
            entry["color"] = hits[0][1]["color"]
        elif near:
            # najlepszy wynik poniżej progu: do ręcznej kontroli, czy jakiś baner nie umyka
            entry["near"] = near
            entry["near_sec"] = near_sec
        return entry
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="najwyżej tyle filmów w tym przebiegu")
    ap.add_argument("--id", action="append", default=[], help="skanuj tylko te filmy")
    ap.add_argument("--rescan", action="store_true", help="skanuj ponownie także już sprawdzone")
    ap.add_argument("--budget", type=int, default=TIME_BUDGET_S, help="limit czasu przebiegu w sekundach")
    ap.add_argument("--no-views", action="store_true", help="nie odświeżaj wyświetleń filmów z banerem")
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
            if v.get("t") and e.get("t_src") != "page":
                e["t"] = v["t"]
            if v.get("views") is not None:
                e["views"] = max(v["views"], e.get("views") or 0)

    todo = []
    for v in videos:
        e = banners.get(v["id"])
        if args.id and v["id"] not in args.id:
            continue
        if e is None or args.rescan or args.id:
            todo.append(v)
        elif "banner" not in e and should_retry(e, v):
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
        prev = banners.get(vid, {})
        base = {"title": v.get("title", ""), "views": max(v.get("views") or 0, prev.get("views") or 0), "t": v.get("t")}
        try:
            entry = scan_video(vid)
            entry.update(base)
            banners[vid] = entry
            log(f"{vid}  {'BANER' if entry['banner'] else 'brak'}  ({entry['frames']} klatek)  {base['title'][:50]}")
        except Exception as exc:  # noqa: BLE001
            if "banner" in prev:   # ponowny skan się nie udał: zostaje poprzedni wynik
                warn(f"{vid}: {exc} (zostaje poprzedni wynik)")
            else:
                tries = prev.get("tries", 0) + 1
                banners[vid] = {**base, "error": str(exc)[:200], "reason": error_reason(str(exc)),
                                "tries": tries, "last_try": int(time.time())}
                warn(f"{vid}: {exc} (próba {tries})")
        done += 1
        if done % 5 == 0:
            save_banners(banners)
    save_banners(banners)
    log(f"Gotowe: sprawdzono {done}. Z banerem łącznie: {sum(1 for e in banners.values() if e.get('banner'))}")
    if not args.no_views:
        refresh_views(banners)
        save_banners(banners)
    return 0


if __name__ == "__main__":
    sys.exit(main())
