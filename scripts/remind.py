#!/usr/bin/env python3
"""Przypomnienia na Telegramie o wysłaniu klipów z banerem do StoreMedia.

Termin to 6 dni od wrzucenia filmu. Czas wrzucenia to górne 32 bity numeru filmu (id >> 32),
a nie godzina, o której bot wykrył film. Po terminie, jeśli klipu nie ma w data/storemedia.json
→ "wyslane", bot wysyła jedno przypomnienie i zapisuje to w data/reminders.json.

Klipy brane pod uwagę: baner wykryty przez bota (data/banners.json) + overrides.json → "baner"
+ storemedia.json → "dodatkowe" (ukryte i 18+, których bot nie widzi), bez overrides.json →
"bez_baneru" i bez filmów wrzuconych przed storemedia.json → "od".

Cisza nocna: przypomnienie, które wypadnie między QUIET_FROM a QUIET_TO czasu polskiego,
czeka do rana (np. film, który bot zobaczy dopiero po terminie, o 3 w nocy).

Token i czat tylko ze zmiennych środowiska TELEGRAM_BOT_TOKEN i TELEGRAM_CHAT_ID (sekrety repo).
Skrypt nigdy ich nie wypisuje i zawsze kończy się kodem 0, żeby nie zatrzymać bota.

Uruchomienie:
  python scripts/remind.py --dry-run         # pokaż, co by poszło, nic nie wysyłaj
  python scripts/remind.py --test [--id ID]  # wiadomość testowa (+ podgląd przypomnienia dla ID)
"""
import argparse
import calendar
import datetime
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
BANNERS = ROOT / "data" / "banners.json"
OVERRIDES = ROOT / "data" / "overrides.json"
STOREMEDIA = ROOT / "data" / "storemedia.json"
REMINDERS = ROOT / "data" / "reminders.json"
USER = "369_shoter"
STOREMEDIA_URL = "https://rushviral.com/tworca/wspolprace"
DEADLINE_S = 6 * 86400
QUIET_FROM, QUIET_TO = 22, 8   # godziny czasu polskiego


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


def warsaw_midnight(day):
    """Północ czasu polskiego danego dnia ("RRRR-MM-DD") jako timestamp."""
    naive = calendar.timegm(time.strptime(day, "%Y-%m-%d"))
    return naive - warsaw_offset(naive)


def uploaded(vid):
    return int(vid) >> 32


def duration(seconds):
    d, h = divmod(int(seconds // 3600), 24)
    parts = ([f"{d} {'dnia' if d == 1 else 'dni'}"] if d else []) + ([f"{h} h"] if h else [])
    return " ".join(parts) or "chwili"


# ---------- które klipy ----------
def clips():
    """{id: tytuł} klipów z banerem, które czekają na wysyłkę do StoreMedia (bez wysłanych i sprzed "od")."""
    banners = read_json(BANNERS, {})
    over = read_json(OVERRIDES, {})
    sm = read_json(STOREMEDIA, {})
    ids = {k for k, e in banners.items() if e.get("banner")}
    ids |= {str(i) for i in over.get("baner", [])}
    ids |= {str(i) for i in sm.get("dodatkowe", [])}
    ids -= {str(i) for i in over.get("bez_baneru", [])}
    ids -= {str(i) for i in sm.get("wyslane", [])}
    ids = {i for i in ids if i.isdigit()}
    try:
        since = warsaw_midnight(str(sm.get("od") or "2000-01-01"))
    except ValueError:
        warn("storemedia.json: zła data w 'od' (ma być RRRR-MM-DD), pomijam ten filtr")
        since = 0
    return {i: (banners.get(i) or {}).get("title", "") for i in ids if uploaded(i) >= since}


def message(vid, title, now):
    up = uploaded(vid)
    deadline = up + DEADLINE_S
    lines = ["📤 Wyślij klip do StoreMedia"]
    if title:
        lines.append(f"„{title}”")
    lines += [
        f"https://www.tiktok.com/@{USER}/video/{vid}",
        "",
        f"Wrzucony: {fmt(up)}",
        f"Termin (6 dni): {fmt(deadline)}" + (f" (po terminie od {duration(now - deadline)})" if now - deadline >= 3600 else ""),
        "",
        "Klip nie jest oznaczony jako wysłany do StoreMedia. Wyślij go, żeby został rozliczony:",
        STOREMEDIA_URL,
        "",
        "Po wysłaniu dopisz numer filmu do data/storemedia.json → \"wyslane\".",
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


# ---------- główne ----------
def run_test(vid):
    now = int(time.time())
    try:
        send(f"✅ Klipometr: test powiadomień działa ({fmt(now)}).")
        log("Wysłano wiadomość testową.")
        if vid:
            if not vid.isdigit():
                warn("--id musi być numerem filmu")
            else:
                send("[TEST, podgląd]\n" + message(vid, read_json(BANNERS, {}).get(vid, {}).get("title", ""), now))
                log(f"Wysłano podgląd przypomnienia dla {vid}.")
    except TelegramError as exc:
        warn(f"Telegram: {exc}")
        return 1
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
    args = ap.parse_args()
    if args.test:
        return run_test(args.id.strip())

    now = int(time.time())
    sent = read_json(REMINDERS, {})
    waiting = clips()
    due = sorted(i for i in waiting if uploaded(i) + DEADLINE_S <= now and i not in sent)
    upcoming = sorted((uploaded(i) + DEADLINE_S, i) for i in waiting if uploaded(i) + DEADLINE_S > now)
    log(f"StoreMedia: czeka na wysyłkę {len(waiting)}, po terminie bez przypomnienia {len(due)}"
        + (f", najbliższy termin {fmt(upcoming[0][0])} ({upcoming[0][1]})" if upcoming else ""))
    if not due:
        return 0
    if args.dry_run:
        for vid in due:
            log("---\n" + message(vid, waiting[vid], now))
        return 0
    hour = warsaw(now).hour
    if hour >= QUIET_FROM or hour < QUIET_TO:
        log(f"Cisza nocna ({QUIET_FROM}:00–{QUIET_TO}:00): {len(due)} przypomnień czeka do rana.")
        return 0
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        log("Brak sekretów TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID, przypomnienia nie wychodzą.")
        return 0
    for vid in due:
        try:
            send(message(vid, waiting[vid], now))
        except TelegramError as exc:
            warn(f"Telegram ({vid}): {exc}")
            break   # zwykle błąd tokena/czatu albo sieci: spróbujemy przy następnym przebiegu
        sent[vid] = {"sent": now}
        REMINDERS.write_text(json.dumps(dict(sorted(sent.items(), reverse=True)), indent=1) + "\n", encoding="utf-8")
        log(f"Przypomnienie wysłane: {vid}")
        time.sleep(1)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001  (przypomnienia nigdy nie zatrzymują bota)
        warn(f"remind.py: {type(exc).__name__}: {exc}")
        sys.exit(0)
