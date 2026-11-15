#!/usr/bin/env python3
"""Run a folder of Playwright tests against a finished ArcFlash output, the way the grader would.

Usage: python3 scripts/grade_local.py <app-dir> <tests-dir> [--port 3311] [--keep]

Copies <app-dir> (without node_modules, .arc and backend/data) to a temporary folder and starts it with
`PORT=<port> bash deploy.sh`; copies <tests-dir> to a second temporary folder, adds a playwright.config.ts unless
one exists, installs @playwright/test there and runs it with BASE_URL set. Prints the pass count, stops the server
and removes both folders unless --keep. Never changes <app-dir> or <tests-dir>.
"""
import argparse
import json
import re
import shutil
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arcflash import config, proc  # noqa: E402
from arcflash.harness import choose_playwright_version, use_playwright_version  # noqa: E402

CONFIG = """import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  workers: 1,
  retries: 0,
  timeout: 30000,
  expect: { timeout: 5000 },
  reporter: [["list"]],
  use: { baseURL: "http://127.0.0.1:%d", headless: true },
});
"""


def copy_app(app_dir: Path, target: Path) -> None:
    def ignore(folder, names):
        skip = {"node_modules", ".arc"} & set(names)
        if Path(folder).resolve() == (app_dir / "backend").resolve() and "data" in names:
            skip.add("data")
        return skip
    shutil.copytree(app_dir, target, ignore=ignore, symlinks=True)


def count(output: str, word: str) -> int:
    found = re.findall(rf"^\s*(\d+) {word}\b", output, re.M)
    return int(found[-1]) if found else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("app_dir", type=Path, help="a finished output folder (with deploy.sh)")
    parser.add_argument("tests_dir", type=Path, help="a folder of Playwright *.spec.ts files")
    parser.add_argument("--port", type=int, default=3311, help="port for the app (default 3311)")
    parser.add_argument("--keep", action="store_true", help="keep the temporary folders")
    args = parser.parse_args(argv)
    app_dir, tests_dir = args.app_dir.resolve(), args.tests_dir.resolve()
    if not (app_dir / "deploy.sh").is_file() or not tests_dir.is_dir():
        print("need an app folder with deploy.sh and a tests folder", file=sys.stderr)
        return 2
    # a server left on the port answers /api/health while deploy.sh still builds, and the tests then run against that
    # other app (2026-11-15: a day-old server on 3451 turned a 13/14 Stack Overflow build into 0/14)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            print(f"[grade] port {args.port} is already in use; stop that server or pick another --port", file=sys.stderr)
            return 2

    work = Path(tempfile.mkdtemp(prefix="arcflash-grade-"))
    app, tests = work / "app", work / "tests"
    base = f"http://127.0.0.1:{args.port}"
    server = None
    try:
        copy_app(app_dir, app)
        print(f"[grade] starting the app on {base} (deploy.sh installs and builds first)", flush=True)
        server = proc.start(["bash", "deploy.sh"], app, env=proc.local_env({"PORT": str(args.port)}), log_path=work / "deploy.log")
        if not proc.wait_for_http(base + "/api/health", 300, server):
            print("[grade] the app did not answer /api/health within 300 s:\n" + proc.tail((work / "deploy.log").read_text(), 40))
            return 1
        shutil.copytree(tests_dir, tests)
        if not (tests / "playwright.config.ts").exists():
            (tests / "playwright.config.ts").write_text(CONFIG % args.port)
        (tests / "package.json").write_text(json.dumps({"private": True}))
        use_playwright_version(tests, choose_playwright_version())
        install = proc.run([proc.npm(), "install", "--no-audit", "--no-fund"], tests, timeout=config.INSTALL_TIMEOUT_SECONDS)
        if install.returncode != 0:
            print("[grade] npm install for the tests failed:\n" + proc.tail(install.output, 30))
            return 1
        cli = ["node", str(tests / "node_modules" / "@playwright" / "test" / "cli.js")]
        proc.run(cli + ["install", "chromium"], tests, timeout=config.BROWSER_INSTALL_TIMEOUT_SECONDS)
        print("[grade] running the tests", flush=True)
        result = proc.run(cli + ["test"], tests, timeout=config.BROWSER_RUN_TIMEOUT_SECONDS,
                          env=proc.local_env({"BASE_URL": base}))
        print(result.output)
        passed, failed = count(result.output, "passed"), count(result.output, "failed")
        flaky, not_run = count(result.output, "flaky"), count(result.output, "did not run")
        total = passed + failed + flaky + not_run
        print(f"[grade] passed {passed} of {total}")
        return 0
    finally:
        if server is not None:
            proc.stop(server)
        if args.keep:
            print(f"[grade] kept {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
