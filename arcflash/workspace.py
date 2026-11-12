"""The output folder: template copy, generated files, versions and shipping."""
from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from arcflash import config, proc
from arcflash.blocks import is_allowed_path

TEMPLATE = Path(__file__).resolve().parent / "template"
PLACEHOLDERS = ("backend/app.js", "frontend/src/shared.jsx", "frontend/src/App.jsx", "frontend/src/pages/main.jsx")
# What ArcFlash itself writes at the top of the output folder and inside .arc/. Everything else there (the runner's
# .arc files, the requirements folder ARC-Bench puts inside the output folder) belongs to someone else and is never touched.
OWN_APP = ("package.json", "package-lock.json", "deploy.sh", "backend", "frontend", "node_modules")
OWN_ARC = re.compile(r"arcflash\.json|harness|versions|check|calls|digest\.txt|notes\.json|run\.json|plan-\d+\.json")
_sleep = time.sleep


class WorkspaceError(RuntimeError):
    pass


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink()


class Workspace:
    def __init__(self, out: Path) -> None:
        self.out = Path(out).resolve()   # absolute: subprocesses run in other folders
        self.arc = self.out / ".arc"
        self.harness = self.arc / "harness"
        self.versions = self.arc / "versions"
        self.check_dir = self.arc / "check"
        self.calls_dir = self.arc / "calls"

    # ---- setup ------------------------------------------------------------------------------

    def init(self, req_dir: Path, assets: list[str]) -> None:
        """Copy the template app, the harness and the assets into out, replacing only what ArcFlash produces."""
        if self.out.exists() and not self.out.is_dir():
            raise WorkspaceError(f"{self.out} exists and is not a folder")
        if self.out.is_dir():
            for name in OWN_APP:
                _remove(self.out / name)
            if self.arc.is_dir():
                for p in self.arc.iterdir():
                    if OWN_ARC.fullmatch(p.name):
                        _remove(p)
        self.out.mkdir(parents=True, exist_ok=True)
        shutil.copytree(TEMPLATE / "app", self.out, dirs_exist_ok=True)
        self.arc.mkdir(exist_ok=True)
        shutil.copytree(TEMPLATE / "harness", self.harness, dirs_exist_ok=True)
        for name in assets:
            target = self.out / "frontend" / "public" / "assets" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(req_dir) / "assets" / name, target)
        self.save_json("arcflash.json", {"agent": "arcflash",
                                         "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})

    def install_app(self) -> proc.ProcResult:
        """npm install in the output folder; one retry after 30 s."""
        args = [proc.npm(), "install", "--include=dev", "--include=optional", "--no-audit", "--no-fund"]
        result = proc.run(args, self.out, timeout=config.INSTALL_TIMEOUT_SECONDS)
        if result.returncode != 0:
            _sleep(30)
            result = proc.run(args, self.out, timeout=config.INSTALL_TIMEOUT_SECONDS)
        return result

    # ---- generated files --------------------------------------------------------------------

    def _disk(self, key: str) -> Path:
        return self.harness / key if key.startswith("tests/") else self.out / key

    def write(self, files: dict[str, str]) -> None:
        for key, content in files.items():
            if not is_allowed_path(key):
                raise WorkspaceError(f"not a generated file key: {key}")
            path = self._disk(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def _generated_paths(self) -> dict[str, Path]:
        found = {}
        candidates = [self.out / k for k in PLACEHOLDERS[:3]] + sorted((self.out / "frontend/src/pages").glob("*.jsx"))
        for path in candidates:
            key = path.relative_to(self.out).as_posix()
            if path.is_file() and is_allowed_path(key):
                found[key] = path
        for path in sorted((self.harness / "tests").glob("scenarios-*.spec.ts")):
            key = "tests/" + path.name
            if is_allowed_path(key):
                found[key] = path
        return found

    def read_generated(self) -> dict[str, str]:
        return {key: path.read_text(encoding="utf-8") for key, path in self._generated_paths().items()}

    def _clear_generated(self) -> None:
        for path in self._generated_paths().values():
            path.unlink()

    def reset_to_template(self) -> None:
        self._clear_generated()
        self.write({key: (TEMPLATE / "app" / key).read_text(encoding="utf-8") for key in PLACEHOLDERS})

    def snapshot(self, label: str, meta: dict) -> Path:
        target = self.versions / label
        _remove(target)
        for key, content in self.read_generated().items():
            path = target / "files" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        target.mkdir(parents=True, exist_ok=True)
        (target / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        return target

    def restore(self, snapshot: Path) -> None:
        files_dir = Path(snapshot) / "files"
        files = {p.relative_to(files_dir).as_posix(): p.read_text(encoding="utf-8")
                 for p in files_dir.rglob("*") if p.is_file()} if files_dir.is_dir() else {}
        self._clear_generated()
        self.write(files)

    # ---- shipping and records ---------------------------------------------------------------

    def ship(self) -> None:
        """Remove what must not ship: the app's database folder, the check folder and the harness's packages."""
        for path in (self.out / "backend" / "data", self.check_dir, self.harness / "node_modules"):
            _remove(path)

    def save_json(self, name: str, data) -> None:
        self.save_text(name, json.dumps(data, ensure_ascii=False, indent=1))

    def save_text(self, name: str, text: str) -> None:
        self.arc.mkdir(parents=True, exist_ok=True)
        (self.arc / name).write_text(text, encoding="utf-8")
