"""ArcFlash entry point: python main.py <requirements-dir> --output-dir <out>."""
import argparse
import sys


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="ArcFlash: generate a web application from a requirements folder.")
    parser.add_argument("requirement_path", help="folder that holds requirements.yaml")
    parser.add_argument("-o", "--output-dir", required=True, help="folder to write the generated application into")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    parse_args(argv)
    print("not implemented yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
