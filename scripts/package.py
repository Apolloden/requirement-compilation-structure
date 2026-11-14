#!/usr/bin/env python3
"""Zip the ArcLaser agent (package arcflash) for ARC-Bench: main.py, requirements.txt and arcflash/ only, file modes kept.

Usage: python3 scripts/package.py [-o PATH]   (default: ../arclaser-<YYYY-MM-DD>.zip, next to the agent folder)
No .env, no tests, no outputs and no ZIP ever goes into the archive, and the archive is never written inside the agent folder.
"""
import argparse
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def agent_files(root: Path) -> list[tuple[Path, str]]:
    files = [(root / "main.py", "main.py"), (root / "requirements.txt", "requirements.txt")]
    for path in sorted((root / "arcflash").rglob("*")):
        rel = path.relative_to(root)
        # no hidden files (.env, .DS_Store, ...), no caches
        if path.is_file() and "__pycache__" not in rel.parts and path.suffix != ".pyc" \
                and not any(part.startswith(".") for part in rel.parts):
            files.append((path, rel.as_posix()))
    return files


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output", type=Path, default=ROOT.parent / f"arclaser-{date.today().isoformat()}.zip",
                        help="archive path (default: ../arclaser-<date>.zip)")
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    if output == ROOT or ROOT in output.parents:
        print(f"refusing to write {output}: the archive must not be inside the agent folder {ROOT}", file=sys.stderr)
        return 1
    files = agent_files(ROOT)
    missing = [rel for path, rel in files if not path.is_file()]
    if missing:
        print(f"missing: {', '.join(missing)}", file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, rel in files:
            archive.write(path, rel)   # ZipInfo.from_file keeps the file mode, so deploy.sh stays executable
    print(f"wrote {output}: {len(files)} entries, {output.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
