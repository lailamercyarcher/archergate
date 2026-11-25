#!/usr/bin/env python3
"""Install open-source IBM Plex Sans fonts into ArcherGate web assets at build time.

A deliberate local-first build step: no CDN dependency when running the app.
This package does not include font files. Refer to the upstream IBM Plex OFL.
"""
from pathlib import Path
from urllib.request import urlopen
import hashlib
import os

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "archergate" / "studio" / "web_assets"
UPSTREAM = "https://raw.githubusercontent.com/IBM/plex/master/packages/plex-sans/fonts/complete/woff2/"
FONTS = ("IBMPlexSans-Regular.woff2", "IBMPlexSans-SemiBold.woff2")

def main():
    DEST.mkdir(parents=True, exist_ok=True)
    for name in FONTS:
        target = DEST / name
        if target.is_file():
            print(f"already installed: {name}")
            continue
        with urlopen(UPSTREAM + name, timeout=50) as response:
            data = response.read(400_000)
        if not (20_000 <= len(data) < 400_000 and data.startswith(b'wOF2')):
            raise ValueError("unexpected font response; no file installed")
        stage = target.with_suffix(".tmp")
        stage.write_bytes(data)
        os.replace(stage, target)
        print(f"installed {name}, sha256 {hashlib.sha256(data).hexdigest()}")

if __name__ == "__main__":
    main()