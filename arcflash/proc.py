"""Subprocesses, free ports and health polling. Every child and every local request bypasses proxies."""
from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

LOCAL_HOSTS = ("127.0.0.1", "localhost")


@dataclass
class ProcResult:
    returncode: int
    output: str
    seconds: float
    timed_out: bool = False


def local_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """os.environ with 127.0.0.1 and localhost added to NO_PROXY and no_proxy, plus extra."""
    env = dict(os.environ)
    for key in ("NO_PROXY", "no_proxy"):
        hosts = [h for h in env.get(key, "").split(",") if h.strip()]
        env[key] = ",".join(hosts + [h for h in LOCAL_HOSTS if h not in hosts])
    env.update(extra or {})
    return env


def _kill_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run(args: list[str], cwd: Path, *, timeout: float, env: dict[str, str] | None = None) -> ProcResult:
    """Run to completion with stdout and stderr merged; on timeout the whole process group is killed."""
    started = time.monotonic()
    try:
        p = subprocess.Popen(args, cwd=cwd, env=env if env is not None else local_env(), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                             start_new_session=True)
    except OSError as e:
        return ProcResult(127, f"could not start {args[0]}: {e}", time.monotonic() - started)
    try:
        output, _ = p.communicate(timeout=timeout)
        return ProcResult(p.returncode, output, time.monotonic() - started)
    except subprocess.TimeoutExpired:
        _kill_group(p.pid, signal.SIGKILL)
        output, _ = p.communicate()
        return ProcResult(p.returncode if p.returncode is not None else -9,
                          (output or "") + f"\n(timed out after {timeout:.0f} s)", time.monotonic() - started, True)


def start(args: list[str], cwd: Path, *, env: dict[str, str], log_path: Path) -> subprocess.Popen:
    """Start in a new process group with all output going to log_path."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as log:
        return subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)


def stop(process: subprocess.Popen) -> None:
    """SIGTERM to the process group, up to 5 s, then SIGKILL to whatever is left."""
    _kill_group(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    _kill_group(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_http(url: str, timeout: float, process: subprocess.Popen | None = None) -> bool:
    """True once url answers 200; False on timeout or as soon as the process exits. No proxies."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            return False
        try:
            with opener.open(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.25)
    return False


def tail(text: str, lines: int = 60) -> str:
    return "\n".join(text.rstrip("\n").split("\n")[-lines:])


def npm() -> str:
    path = shutil.which("npm")
    if not path:
        raise FileNotFoundError("npm is not on PATH")
    return path
