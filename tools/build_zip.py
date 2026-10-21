#!/usr/bin/env python3
"""
Build the zip that KiCad's Plugin and Content Manager installs with
"Install from File...":

    python tools/build_zip.py        # -> dist/kicad-timelapse-<version>.zip

Layout inside the zip: metadata.json, plugins/, resources/icon.png.
"""
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    meta = json.loads((ROOT / "metadata.json").read_text(encoding="utf-8"))
    version = meta["versions"][0]["version"]
    out = ROOT / "dist" / f"kicad-timelapse-{version}.zip"
    out.parent.mkdir(exist_ok=True)
    files = [ROOT / "metadata.json", ROOT / "resources" / "icon.png"]
    files += sorted(p for p in (ROOT / "plugins").iterdir() if p.suffix in (".py", ".png"))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(ROOT).as_posix())
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
