"""The ArcLaser flow: read → tests and screenshot notes in the background, plan at once → code (with the notes) →
check → repair → restart once → ship."""
from __future__ import annotations

import json
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from arcflash import config
from arcflash.check import CheckReport, run_check
from arcflash.generate import GenerationError, generate_app, write_tests
from arcflash.harness import HarnessStatus, prepare_harness
from arcflash.llm import LLM, LLMError, Usage
from arcflash.plan import PlanError, make_plan
from arcflash.proc import ProcResult
from arcflash.prompting import build_context, notes_block
from arcflash.repair import WORSE_NOTE, repair_loop
from arcflash.spec import SpecError, load_spec
from arcflash.vision import describe_images
from arcflash.workspace import Workspace, WorkspaceError


@dataclass
class Tools:
    install_app: Callable[[Workspace], ProcResult]
    prepare_harness: Callable[[Path], HarnessStatus]
    check: Callable[[Workspace, dict, bool], CheckReport]


def default_tools() -> Tools:
    return Tools(install_app=lambda ws: ws.install_app(), prepare_harness=prepare_harness,
                 check=lambda ws, plan, ok: run_check(ws, plan, harness_ok=ok))


class Budget:
    """Time and estimated billed cost. Repairs stop at MAX_RUN_SECONDS, except a rescue round for an app that does not
    start (it would score 0); a restart needs as much time as the first attempt took."""

    def __init__(self, usage: Usage, scenarios: int, *, clock=time.monotonic) -> None:
        self.usage = usage
        self.clock = clock
        self.started = clock()
        self.cap_cny = max(config.MIN_RUN_COST_CNY, config.COST_PER_SCENARIO_CNY * scenarios)
        self.attempt_seconds = 0.0   # how long the last attempt took, from its plan to its last check

    def elapsed(self) -> float:
        return self.clock() - self.started

    def _billed(self) -> float:
        return self.usage.totals()["billed_cny"]

    def _attempt_reserve(self) -> float:
        return max(config.ATTEMPT_RESERVE_SECONDS, self.attempt_seconds)

    def allows(self, kind: str) -> bool:
        if kind == "repair":
            return self.elapsed() + config.REPAIR_RESERVE_SECONDS <= config.MAX_RUN_SECONDS and self._billed() < self.cap_cny
        if kind == "rescue":
            return self.elapsed() <= config.RESCUE_UNTIL_SECONDS and self._billed() < self.cap_cny
        if kind == "attempt":
            return (self.elapsed() + self._attempt_reserve() <= config.MAX_RUN_SECONDS
                    and self._billed() < self.cap_cny * config.RESTART_COST_SHARE)
        raise ValueError(f"unknown budget kind {kind!r}")

    def stop_reason(self) -> str | None:
        minutes, billed = self.elapsed() / 60, self._billed()
        used = f"{minutes:.1f} of {config.MAX_RUN_SECONDS / 60:.0f} minutes used"
        if self.elapsed() + config.REPAIR_RESERVE_SECONDS > config.MAX_RUN_SECONDS:
            return f"time: {used}"
        if billed >= self.cap_cny:
            return f"cost: estimated ¥{billed:.2f} reached the cap of ¥{self.cap_cny:.2f}"
        if self.elapsed() + self._attempt_reserve() > config.MAX_RUN_SECONDS:
            return f"time: {used}; too little left for a restart ({self._attempt_reserve() / 60:.0f} min)"
        if billed >= self.cap_cny * config.RESTART_COST_SHARE:
            return f"cost: estimated ¥{billed:.2f}; a restart needs less than ¥{self.cap_cny * config.RESTART_COST_SHARE:.2f}"
        return None


class Timeline:
    """Phases with start and end in seconds since the run started (Task 12)."""

    def __init__(self, clock, start: float) -> None:
        self.clock, self.start, self.phases, self._lock = clock, start, [], threading.Lock()

    def add(self, phase: str, t0: float, t1: float) -> None:
        with self._lock:
            self.phases.append({"phase": phase, "start": round(t0 - self.start, 1), "end": round(t1 - self.start, 1)})

    @contextmanager
    def span(self, phase: str):
        t0 = self.clock()
        try:
            yield
        finally:
            self.add(phase, t0, self.clock())

    def sorted(self) -> list[dict]:
        with self._lock:
            return sorted(self.phases, key=lambda p: (p["start"], p["end"]))


def _module_of(purpose: str) -> str:
    return purpose.removeprefix("frontend:").split("+")[0].removesuffix("-again")


def critical_output_tokens(records: list[dict]) -> int:
    """Output tokens of the calls that run one after another: per attempt (a new one starts at each "plan" call) the plan,
    the longer of the backend and the shell plus the longest page module, and every repair and quick-fix call. Notes and
    the test writer run beside them and do not count. Runtime is this number divided by the endpoint's speed."""
    total, segment = 0, None
    def close(seg):
        if seg is None:
            return 0
        code = max(seg["backend"], seg["shell"] + max(seg["modules"].values(), default=0))
        return seg["plan"] + code + seg["repair"]
    for r in records:
        purpose, out = str(r.get("purpose", "")), int(r.get("output", 0) or 0)
        if purpose == "plan" or segment is None:
            total += close(segment)
            segment = {"plan": 0, "backend": 0, "shell": 0, "modules": {}, "repair": 0}
        if purpose.startswith("plan"):
            segment["plan"] += out
        elif purpose.startswith("backend"):
            segment["backend"] += out
        elif purpose.startswith("frontend:shell"):
            segment["shell"] += out
        elif purpose.startswith("frontend:"):
            mid = _module_of(purpose)
            segment["modules"][mid] = segment["modules"].get(mid, 0) + out
        elif purpose.startswith(("repair:", "quick_fix:")):
            segment["repair"] += out
    return total + close(segment)


def good_enough(report: CheckReport) -> bool:
    return report.booted and (report.scenario_total == 0
                              or report.scenario_passed / report.scenario_total >= config.GOOD_ENOUGH_PASS_RATIO)


def _say(text: str) -> None:
    print(f"[arclaser] {text}", flush=True)


def _safe_result(future, fallback):
    try:
        return future.result()
    except Exception as e:  # a helper that crashed counts as failed, it never stops the run
        return fallback(e)


def _rounds(ws: Workspace, label: str) -> list[dict]:
    keys = ("label", "built", "booted", "scenarios_passed", "scenarios_total", "smoke_passed", "smoke_total", "static_issues")
    metas = []
    for meta_file in ws.versions.glob(f"{label}-r*/meta.json"):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        metas.append((int(meta_file.parent.name.rsplit("-r", 1)[1]), {k: meta.get(k) for k in keys}))
    return [m for _, m in sorted(metas, key=lambda item: item[0])]


def _shipped(source: str, report: CheckReport | None) -> dict:
    if report is None:
        return {"source": source, "built": False, "booted": False, "scenarios_passed": 0, "scenarios_total": 0}
    return {"source": source, "built": report.built, "booted": report.booted,
            "scenarios_passed": report.scenario_passed, "scenarios_total": report.scenario_total}


def run(req_dir: Path, out_dir: Path, *, llm=None, tools: Tools | None = None, clock=time.monotonic) -> dict:
    """Run the whole flow. Never raises; returns the dict it writes to .arc/run.json."""
    req_dir, out_dir = Path(req_dir), Path(out_dir)
    started = clock()
    tools = tools or default_tools()
    result = {"agent": "arclaser", "started_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "seconds": 0,
              "input": {"requirements": 0, "leaves": 0, "scenarios": 0, "images": 0, "assets": 0},
              "usage": Usage().totals(), "calls": [], "harness_ok": False, "test_problems": [], "attempts": [],
              "shipped": _shipped("template", None), "stop_reason": None, "errors": []}
    ws = Workspace(out_dir)

    # 1. read the folder and set up the output
    spec = None
    try:
        spec = load_spec(req_dir)
    except SpecError as e:
        result["errors"].append(str(e))
        _say(f"cannot read the requirements: {e}")
    try:
        ws.init(req_dir, spec.assets if spec else [])
    except (WorkspaceError, OSError) as e:
        result["errors"].append(str(e))
        _say(f"cannot use the output folder: {e}")
        return result
    if spec is not None:
        ws.save_text("digest.txt", spec.digest)
        result["input"] = {"requirements": len(spec.requirements), "leaves": len(spec.leaves),
                           "scenarios": len(spec.scenarios), "images": len(spec.images), "assets": len(spec.assets)}
        _say(f"read {len(spec.leaves)} leaf requirements, {len(spec.scenarios)} scenarios, {len(spec.images)} screenshots")
        try:
            llm = llm or LLM(log_dir=ws.calls_dir, clock=clock)
        except LLMError as e:
            result["errors"].append(str(e))
            _say(f"no model: {e}")
            llm = None
    state = {"versions": [], "unchecked": False}
    if spec is not None and llm is not None:
        try:
            _flow(spec, req_dir, ws, llm, tools, clock, result, state)
        except Exception as e:  # last resort: whatever happened, the best version or the template ships
            result["errors"].append(f"unexpected error: {e!r}")
            _say("unexpected error:\n" + traceback.format_exc())
    _ship(ws, state, result)

    if llm is not None:
        result["usage"], result["calls"] = llm.usage.totals(), llm.usage.records()
        result["critical_output_tokens"] = critical_output_tokens(result["calls"])
    if "timeline" in state:
        result["timeline"] = state["timeline"].sorted()
    result["seconds"] = round(clock() - started, 1)
    ws.save_json("run.json", result)
    u, s = result["usage"], result["shipped"]
    _say(f"shipped {s['source']}: local scenarios {s['scenarios_passed']}/{s['scenarios_total']}; {u['calls']} calls, "
         f"{u['output']} output tokens, estimated billed ¥{u['billed_cny']:.2f}, {result['seconds'] / 60:.1f} min")
    return result


def _ship(ws: Workspace, state: dict, result: dict) -> None:
    """7. The best version that started, else the best that built, else the template; then remove test data."""
    if state["unchecked"]:
        ws.ship()
        result["shipped"] = _shipped("unchecked", None)
        return
    for condition in (lambda r: r.booted, lambda r: r.built):
        candidates = [v for v in state["versions"] if condition(v[0])]
        if candidates:
            report, snapshot = max(candidates, key=lambda v: v[0].rank())
            ws.restore(snapshot)
            ws.ship()
            result["shipped"] = _shipped(snapshot.name, report)
            return
    ws.reset_to_template()
    ws.ship()
    result["shipped"] = _shipped("template", None)


def _flow(spec, req_dir: Path, ws: Workspace, llm, tools: Tools, clock, result: dict, state: dict) -> None:
    """Steps 2 to 6; every checked version is added to state["versions"] as soon as it exists."""
    budget = Budget(llm.usage, len(spec.scenarios), clock=clock)
    timeline = state["timeline"] = Timeline(clock, budget.started)
    versions: list[tuple[CheckReport, Path]] = state["versions"]
    resumable: list[tuple] = []   # (report, snapshot, plan, context, harness_ok, attempt number) per finished attempt
    with ThreadPoolExecutor(max_workers=4) as pool:
        # 2-3. background installs, the independent tests and the screenshot notes; the plan does not wait for the notes
        install_future = pool.submit(tools.install_app, ws)
        harness_future = pool.submit(tools.prepare_harness, ws.out)
        tests_future = pool.submit(write_tests, spec, llm)
        notes: dict[str, str] = {}
        notes_started = clock()
        notes_future = pool.submit(describe_images, spec, req_dir, llm, notes)
        notes_future.add_done_callback(lambda f: timeline.add("notes", notes_started, clock()))
        _say("installing packages, writing tests and screenshot notes in the background")
        notes_text: str | None = None
        tests: dict[str, str] | None = None

        # 4-6. attempts
        for n in range(1, config.MAX_ATTEMPTS + 1):
            if n > 1:
                if versions and good_enough(versions[0][0]):
                    break
                if not budget.allows("attempt"):
                    result["stop_reason"] = budget.stop_reason()
                    _say(f"no restart: {result['stop_reason']}")
                    break
                _say("restarting from the plan")
            attempt = {"attempt": n, "plan_problems": [], "generation_problems": [], "error": None, "rounds": []}
            result["attempts"].append(attempt)
            temperature = config.TEMPERATURE if n == 1 else config.RESTART_TEMPERATURE
            attempt_started = clock()
            try:
                _say(f"attempt {n}: plan")
                with timeline.span(f"plan a{n}"):
                    plan, attempt["plan_problems"] = make_plan(spec, "", llm, temperature=temperature)
                ws.save_json(f"plan-{n}.json", plan)
                if notes_text is None:   # code uses the layout hints of the notes: wait for them, at most NOTES_WAIT_SECONDS
                    try:
                        notes_future.result(timeout=max(0.0, config.NOTES_WAIT_SECONDS - (clock() - notes_started)))
                    except Exception:
                        _say("screenshot notes not complete; code generation goes on with the notes that exist")
                    done = dict(notes)
                    ws.save_json("notes.json", done)
                    notes_text = notes_block(spec, done)
                _say(f"attempt {n}: backend and frontend")
                with timeline.span(f"code a{n}"):
                    files, attempt["generation_problems"] = generate_app(spec, notes_text, plan, llm, temperature=temperature)
            except (PlanError, GenerationError, LLMError) as e:
                attempt["error"] = str(e)
                result["errors"].append(f"attempt {n}: {e}")
                _say(f"attempt {n} failed: {e}")
                continue
            if tests is None:
                waited = clock()
                tests, result["test_problems"] = _safe_result(tests_future, lambda e: ({}, [f"test writer failed: {e!r}"]))
                result["test_wait_seconds"] = round(clock() - waited, 1)
                _say(f"waited {result['test_wait_seconds']:.0f} s for the test writer")
            ws.replace_app({**files, **tests})
            install = _safe_result(install_future, lambda e: ProcResult(127, repr(e), 0.0))
            harness = _safe_result(harness_future, lambda e: HarnessStatus(False, repr(e)))
            if install.returncode != 0:
                result["errors"].append("npm install failed; the app ships unchecked:\n" + install.output[-2000:])
                _say("npm install failed; shipping the generated app unchecked")
                state["unchecked"] = True
                break
            result["harness_ok"] = harness_ok = harness.ok
            if not harness_ok:
                result["errors"].append("the browser harness is not available; checks stop after the start:\n" + harness.log[-2000:])
            _say(f"attempt {n}: check")
            with timeline.span(f"check a{n}-r0"):
                first = tools.check(ws, plan, harness_ok)
            context = build_context(spec, notes_text, plan)
            best, snapshot = repair_loop(ws, context, plan, llm, first, lambda: tools.check(ws, plan, harness_ok), budget, f"a{n}",
                                         timeline=timeline)
            budget.attempt_seconds = clock() - attempt_started
            attempt["rounds"] = _rounds(ws, f"a{n}")
            versions.append((best, snapshot))
            resumable.append((best, snapshot, plan, context, harness_ok, n))
            _say(f"attempt {n}: best {snapshot.name}, scenarios {best.scenario_passed}/{best.scenario_total}")

        # 6b. a restart that ended before it had an app (plan cut or unusable, model down) would leave the rest of the
        # time unused: repair the best version further instead, told that the earlier rounds on it gained nothing
        if len(result["attempts"]) > 1 and result["attempts"][-1]["error"] and resumable and budget.allows("repair"):
            report, snapshot, plan, context, harness_ok, n = max(resumable, key=lambda v: v[0].rank())
            ws.restore(snapshot)
            _say(f"the restart failed; repairing {snapshot.name} further")
            attempt = {"attempt": f"{n}c", "resumed_from": snapshot.name, "plan_problems": [], "generation_problems": [],
                       "error": None, "rounds": []}
            result["attempts"].append(attempt)
            best, snapshot = repair_loop(ws, context, plan, llm, report, lambda: tools.check(ws, plan, harness_ok), budget,
                                         f"a{n}c", timeline=timeline, note=WORSE_NOTE)
            attempt["rounds"] = _rounds(ws, f"a{n}c")
            versions.append((best, snapshot))
            _say(f"attempt {n}c: best {snapshot.name}, scenarios {best.scenario_passed}/{best.scenario_total}")
