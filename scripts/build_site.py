#!/usr/bin/env python3
"""Składa stronę do opublikowania w folderze _site.

Lista plików jest tutaj, a nie w pliku workflow: przebieg bota uruchomiony przed zmianą ma starą
wersję workflow, ale przed publikacją pobiera najnowsze repo, więc zawsze użyje aktualnej listy.
"""
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "_site"
FILES = ["index.html", "manifest.webmanifest", "sw.js"]
DIRS = ["data", "icons"]


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    for f in FILES:
        shutil.copy2(ROOT / f, OUT / f)
    for d in DIRS:
        shutil.copytree(ROOT / d, OUT / d)
    (OUT / ".nojekyll").write_text("")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "payouts.py"), str(OUT / "data" / "payouts.json")], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "remind.py"), "--build", str(OUT / "data" / "storemedia.json")], check=True)
    missing = [f for f in FILES + ["icons/icon-192.png", "icons/icon-512.png"] if not (OUT / f).exists()]
    if missing:
        sys.exit("Brakuje plików: " + ", ".join(missing))
    print("Strona złożona:", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
