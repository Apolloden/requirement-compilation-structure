"""ArcFlash entry point: python main.py <requirements-dir> --output-dir <out>. Always exits with code 0."""
import argparse
import sys
import traceback
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="ArcFlash: generate a web application from a requirements folder.")
    parser.add_argument("requirement_path", help="folder that holds requirements.yaml")
    parser.add_argument("-o", "--output-dir", required=True, help="folder to write the generated application into")
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        print(f"[arcflash] ignoring unknown arguments: {' '.join(unknown)}", flush=True)
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        from dotenv import load_dotenv
        # Values injected by ARC-Bench always win over a local .env.
        load_dotenv(Path(__file__).with_name(".env"), override=False)
        from arcflash.pipeline import run
        run(Path(args.requirement_path), Path(args.output_dir))
    except Exception:
        print("[arcflash] unexpected error:\n" + traceback.format_exc(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
