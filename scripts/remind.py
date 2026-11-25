#!/usr/bin/env python3
"""Przypomnienia na Telegramie o wysłaniu klipów do StoreMedia.

Klipy dodajesz sam na stronie (sekcja „Wysyłka do StoreMedia”): strona tworzy na GitHubie plik
  data/storemedia/<czas>.json  ->  {"link": "https://vm.tiktok.com/...", "added": "..."}
Bot przy każdym przebiegu:
  1. zamienia link (też skrócony z telefonu) na numer filmu i link przeglądarkowy
     https://www.tiktok.com/@<konto>/video/<numer>,
  2. liczy czas wrzucenia z numeru filmu (górne 32 bity, id >> 32) i termin = wrzucenie + 6 dni,
  3. po terminie wysyła JEDNO przypomnienie na Telegram i zapisuje to w data/reminders.json.
Usunięcie pliku z data/storemedia/ (na stronie „zmień / usuń”) kończy śledzenie klipu.

Cisza nocna: przypomnienie, które wypadnie między QUIET_FROM a QUIET_TO czasu polskiego, czeka
do rana (np. klip dodany już po terminie o 23:00 albo termin wypadający w nocy).

Token i czat tylko ze zmiennych środowiska TELEGRAM_BOT_TOKEN i TELEGRAM_CHAT_ID (sekrety repo).
Skrypt nigdy ich nie wypisuje i zawsze kończy się kodem 0, żeby nie zatrzymać bota.

Uruchomienie:
  python scripts/remind.py                   # przebieg bota
  python scripts/remind.py --dry-run         # pokaż, co by poszło, nic nie wysyłaj i nie zapisuj
  python scripts/remind.py --test [--id ID]  # wiadomość testowa (+ podgląd przypomnienia dla ID)
  python scripts/remind.py --build PLIK      # lista klipów dla strony (bez sieci, przy publikacji)
"""
import argparse
import calendar
import datetime
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
BANNERS = ROOT / "data" / "banners.json"
SRC = ROOT / "data" / "storemedia"
REMINDERS = ROOT / "data" / "reminders.json"
USER = "369_shoter"
STOREMEDIA_URL = "https://rushviral.com/tworca/wspolprace"
DEADLINE_S = 6 * 86400
QUIET_FROM, QUIET_TO = 22, 8   # godziny czasu polskiego
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIKTOK_HOST = re.compile(r"^([a-z0-9-]+\.)*tiktok\.com$", re.I)
VIDEO_ID = re.compile(r"/(?:video|photo|v)/(\d{15,22})")
ACCOUNT = re.compile(r"/@([\w.]+)")


def log(msg):
    print(msg, flush=True)


def warn(msg):
    print(("::warning::" if os.environ.get("GITHUB_ACTIONS") else "UWAGA: ") + msg, flush=True)


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


# ---------- czas polski (bez zależności od bazy stref czasowych) ----------
def _last_sunday_utc(year, month, hour):
    day = max(d for d in range(1, calendar.monthrange(year, month)[1] + 1)
              if datetime.date(year, month, d).weekday() == 6)
    return calendar.timegm((year, month, day, hour, 0, 0))


def warsaw_offset(ts):
    """CEST (+2 h) od ostatniej niedzieli marca 01:00 UTC do ostatniej niedzieli października 01:00 UTC, poza tym CET (+1 h)."""
    year = time.gmtime(ts).tm_year
    summer = _last_sunday_utc(year, 3, 1) <= ts < _last_sunday_utc(year, 10, 1)
    return 7200 if summer else 3600


def warsaw(ts):
    return datetime.datetime.fromtimestamp(ts + warsaw_offset(ts), datetime.timezone.utc).replace(tzinfo=None)


def fmt(ts):
    return warsaw(ts).strftime("%d.%m.%Y, %H:%M:%S")


def uploaded(vid):
    return int(vid) >> 32


def duration(seconds):
    d, h = divmod(int(seconds // 3600), 24)
    parts = ([f"{d} {'dnia' if d == 1 else 'dni'}"] if d else []) + ([f"{h} h"] if h else [])
    return " ".join(parts) or "chwili"


def browser_link(vid, account=USER):
    return f"https://www.tiktok.com/@{account or USER}/video/{vid}"


# ---------- linki ----------
def normalize(link):
    """Link z https://, albo ValueError, gdy to nie TikTok."""
    link = link.strip()
    if not re.match(r"^https?://", link, re.I):
        link = "https://" + link
    if not TIKTOK_HOST.match(urllib.parse.urlsplit(link).hostname or ""):
        raise ValueError("to nie jest link do TikToka")
    return link


def parse_link(link):
    """(numer filmu, konto) z pełnego linku TikToka, bez sieci. (None, None), gdy to link skrócony albo nie TikTok."""
    try:
        link = normalize(link)
    except ValueError:
        return None, None
    m = VIDEO_ID.search(link)
    if not m:
        return None, None
    a = ACCOUNT.search(link)
    return m.group(1), (a.group(1) if a else None)


def resolve(link):
    """Numer filmu i konto z dowolnego linku TikToka (skrócony vm./vt.tiktok.com albo /t/ – przez przekierowanie)."""
    link = normalize(link)
    vid, account = parse_link(link)
    if vid:
        return vid, account
    req = urllib.request.Request(link, headers={"User-Agent": UA, "Accept-Language": "pl,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        final = resp.geturl()
    vid, account = parse_link(final)
    if not vid:
        raise ValueError("link nie prowadzi do filmu")
    return vid, account


def load_links():
    """{klucz pliku: link} z data/storemedia/*.json (błędny plik pomijamy z ostrzeżeniem)."""
    out = {}
    for f in sorted(SRC.glob("*.json")) if SRC.exists() else []:
        link = str(read_json(f, {}).get("link") or "").strip()
        if link:
            out[f.stem] = link[:500]
        else:
            warn(f"{f.name}: brak pola \"link\", pomijam")
    return out


def entries(links, state):
    """Klipy z numerem filmu: [(klucz, numer, konto)] – z pliku stanu albo prosto z pełnego linku."""
    out = []
    for key, link in links.items():
        s = state.get(key) or {}
        vid, account = (s.get("id"), s.get("account")) if s.get("id") else parse_link(link)
        if vid:
            out.append((key, vid, account))
    return out


def message(vid, account, title, now):
    up = uploaded(vid)
    deadline = up + DEADLINE_S
    lines = ["📤 Wyślij klip do StoreMedia"]
    if title:
        lines.append(f"„{title}”")
    lines += [
        browser_link(vid, account),
        "",
        f"Wrzucony: {fmt(up)}",
        f"Minęło 6 dni: {fmt(deadline)}" + (f" (od {duration(now - deadline)})" if now - deadline >= 3600 else ""),
        "",
        "Wyślij teraz ten klip do StoreMedia, żeby został rozliczony:",
        STOREMEDIA_URL,
    ]
    return "\n".join(lines)


# ---------- Telegram ----------
class TelegramError(Exception):
    pass


def send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise TelegramError("brak sekretów TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            desc = json.loads(exc.read().decode("utf-8")).get("description", "")
        except Exception:  # noqa: BLE001
            desc = ""
        raise TelegramError(f"HTTP {exc.code} {desc}".replace(token, "***")) from None
    except Exception as exc:  # noqa: BLE001  (bez URL-a, żeby token nie trafił do logu)
        raise TelegramError(f"{type(exc).__name__}: {exc}".replace(token, "***")) from None
    if not body.get("ok"):
        raise TelegramError(str(body.get("description", "nieznany błąd")).replace(token, "***"))


# ---------- tryby ----------
def run_test(vid):
    now = int(time.time())
    try:
        send(f"✅ Klipometr: test powiadomień działa ({fmt(now)}).")
        log("Wysłano wiadomość testową.")
        if vid:
            if not vid.isdigit():
                warn("--id musi być numerem filmu")
            else:
                title = read_json(BANNERS, {}).get(vid, {}).get("title", "")
                send("[TEST, podgląd]\n" + message(vid, None, title, now))
                log(f"Wysłano podgląd przypomnienia dla {vid}.")
    except TelegramError as exc:
        warn(f"Telegram: {exc}")
        return 1
    return 0


def run_build(out):
    """Lista dla strony: [{key, link, id, account, sent, error}] (najnowsze dodane najpierw)."""
    links = load_links()
    state = read_json(REMINDERS, {})
    known = {key: (vid, account) for key, vid, account in entries(links, state)}
    items = []
    for key in sorted(links, reverse=True):
        s = state.get(key) or {}
        vid, account = known.get(key, (None, None))
        item = {"key": key, "link": links[key]}
        if vid:
            item.update(id=vid, account=account or USER)
        if s.get("sent"):
            item["sent"] = s["sent"]
        if s.get("error") and not vid:
            item["error"] = s["error"]
        items.append(item)
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"StoreMedia: {len(items)} klipów -> {out}")
    return 0


def run(dry):
    now = int(time.time())
    links = load_links()
    state = read_json(REMINDERS, {})
    before = json.dumps(state, sort_keys=True)
    # usunięte z data/storemedia/ przestajemy śledzić
    state = {k: v for k, v in state.items() if k in links}

    # 1. linki -> numer filmu (skrócone przez przekierowanie TikToka)
    for key, link in links.items():
        s = state.setdefault(key, {})
        if s.get("id"):
            continue
        try:
            vid, account = resolve(link)
            s.update(id=vid, account=account or USER)
            s.pop("error", None)
            log(f"{key}: {link} -> {browser_link(vid, account)} (wrzucony {fmt(uploaded(vid))})")
        except Exception as exc:  # noqa: BLE001
            s["error"] = str(exc)[:200]
            warn(f"{key}: nie udało się odczytać linku {link}: {exc}")

    # 2. przypomnienia po terminie
    titles = read_json(BANNERS, {})
    tracked = [(k, s["id"], s.get("account")) for k, s in state.items() if s.get("id")]
    due = sorted((k, vid, acc) for k, vid, acc in tracked if uploaded(vid) + DEADLINE_S <= now and not state[k].get("sent"))
    upcoming = sorted((uploaded(vid) + DEADLINE_S, vid) for k, vid, acc in tracked if uploaded(vid) + DEADLINE_S > now)
    log(f"StoreMedia: śledzonych {len(tracked)}, do przypomnienia teraz {len(due)}"
        + (f", najbliższy termin {fmt(upcoming[0][0])} ({upcoming[0][1]})" if upcoming else ""))

    def save():
        if not dry and json.dumps(state, sort_keys=True) != before:
            REMINDERS.write_text(json.dumps(dict(sorted(state.items(), reverse=True)), ensure_ascii=False, indent=1) + "\n",
                                 encoding="utf-8")

    if dry:
        for k, vid, acc in due:
            log("---\n" + message(vid, acc, (titles.get(vid) or {}).get("title", ""), now))
        return 0
    hour = warsaw(now).hour
    if due and (hour >= QUIET_FROM or hour < QUIET_TO):
        log(f"Cisza nocna ({QUIET_FROM}:00–{QUIET_TO}:00): {len(due)} przypomnień czeka do rana.")
        due = []
    elif due and (not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID")):
        log("Brak sekretów TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, przypomnienia nie wychodzą.")
        due = []
    for k, vid, acc in due:
        try:
            send(message(vid, acc, (titles.get(vid) or {}).get("title", ""), now))
        except TelegramError as exc:
            warn(f"Telegram ({vid}): {exc}")
            break   # zwykle błąd tokena/czatu albo sieci: spróbujemy przy następnym przebiegu
        state[k]["sent"] = now
        save()
        log(f"Przypomnienie wysłane: {vid}")
        time.sleep(1)
    save()
    return 0


def main():
    try:
        sys.stdout.reconfigure(errors="replace")   # konsola Windows nie zna emoji
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="tylko pokaż, nic nie wysyłaj i nie zapisuj")
    ap.add_argument("--test", action="store_true", help="wyślij wiadomość testową")
    ap.add_argument("--id", default="", help="z --test: dołącz podgląd przypomnienia dla tego filmu")
    ap.add_argument("--build", metavar="PLIK", help="zapisz listę klipów dla strony")
    args = ap.parse_args()
    if args.test:
        return run_test(args.id.strip())
    if args.build:
        return run_build(args.build)
    return run(args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001  (przypomnienia nigdy nie zatrzymują bota)
        warn(f"remind.py: {type(exc).__name__}: {exc}")
        sys.exit(0)
