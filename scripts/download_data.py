"""Download the raw Berka (PKDD'99) files into data/raw/.

    python scripts/download_data.py

Works the same on Windows, Mac and Linux. Files that are already there are
skipped. Then build the database with scripts/build_db.py.
"""
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SOURCE = "https://raw.githubusercontent.com/jlacko/berka-dataset/master"
FILES = ["account", "card", "client", "disp", "district", "loan", "order", "trans"]


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = RAW / f"{name}.asc"
        if target.exists():
            print(f"{target.name:14} already there")
            continue
        print(f"{target.name:14} downloading...", end=" ", flush=True)
        # download to a temp name first, so a broken download never looks finished
        partial = target.with_suffix(".part")
        urllib.request.urlretrieve(f"{SOURCE}/{name}.asc", partial)
        partial.rename(target)
        print(f"{target.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
