#!/usr/bin/env python3
"""Zbiera wypłaty z data/payouts/*.json w jeden plik dla panelu.

Każda wypłata to osobny mały plik (tworzony z panelu przez stronę GitHuba), np.
  data/payouts/1795600000000.json  ->  {"date": "2026-11-25", "pln": 489.9, "note": "..."}
Przy publikacji strony ten skrypt łączy je w data/payouts.json (lista, najnowsze najpierw).
Błędny plik jest pomijany z ostrzeżeniem, więc literówka nigdy nie psuje strony.

Uruchomienie:  python scripts/payouts.py [plik_wyjściowy]
"""
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "payouts"
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "payouts.json"
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def warn(msg):
    print(("::warning::" if os.environ.get("GITHUB_ACTIONS") else "UWAGA: ") + msg)


def main():
    items = []
    for f in sorted(SRC.glob("*.json")) if SRC.exists() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            num = lambda k: float(str(d[k]).replace(",", ".")) if d.get(k) not in (None, "") else None  # noqa: E731
            pln, usd = num("pln"), num("usd")
            date = str(d.get("date", ""))
            if not DATE.match(date) or not any(x and 0 < x < 10_000_000 for x in (pln, usd)):
                raise ValueError("zła data albo kwota")
            item = {"id": f.stem, "date": date, "note": str(d.get("note", ""))[:200]}
            if pln:
                item["pln"] = round(pln, 2)
            if usd:
                item["usd"] = round(usd, 2)
            items.append(item)
        except Exception as exc:  # noqa: BLE001
            warn(f"pomijam {f.name}: {exc}")
    items.sort(key=lambda x: (x["date"], x["id"]), reverse=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Wypłat: {len(items)}, razem {round(sum(i.get('pln', 0) for i in items), 2)} zł")


if __name__ == "__main__":
    main()
