import contextlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from arcflash import proc
from arcflash.workspace import TEMPLATE, Workspace, WorkspaceError
from tests.support import FIXTURES

NOTES = FIXTURES / "specs" / "notes"
TEMPLATE_APP = TEMPLATE / "app"


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.req = self.tmp / "task"
        shutil.copytree(NOTES, self.req)
        (self.req / "assets").mkdir()
        (self.req / "assets" / "logo.png").write_bytes(b"png")
        self.out = self.tmp / "out"

    def make(self, out=None):
        ws = Workspace(out or self.out)
        ws.init(self.req, ["logo.png"])
        return ws

    def test_init_copies_template_assets_and_marker(self):
        out = self.make().out
        for rel in ["package.json", "deploy.sh", "backend/server.js", "backend/app.js", "frontend/src/main.jsx",
                    "frontend/public/assets/logo.png", ".arc/arcflash.json", ".arc/harness/tests/helpers.ts"]:
            self.assertTrue((out / rel).exists(), rel)
        self.assertTrue((out / "deploy.sh").stat().st_mode & 0o111)

    def test_init_refuses_foreign_content(self):
        self.out.mkdir()
        (self.out / "keep.txt").write_text("x")
        with self.assertRaises(WorkspaceError): Workspace(self.out).init(NOTES, [])
        self.assertTrue((self.out / "keep.txt").exists())
        (self.out / "keep.txt").unlink()
        ws = self.make()
        (self.out / "stray.txt").write_text("x")
        (self.out / "frontend/src/pages/old.jsx").write_text("x")
        ws.init(self.req, [])
        self.assertFalse((self.out / "stray.txt").exists()); self.assertFalse((self.out / "frontend/src/pages/old.jsx").exists())
        self.assertTrue((self.out / "backend/server.js").exists())

    def test_init_keeps_runner_files_in_arc(self):
        # The ARC-Bench runner writes into .arc/ before it starts the agent.
        (self.out / ".arc/traceability").mkdir(parents=True)
        (self.out / ".arc/runner-events.jsonl").write_text("{}\n")
        (self.out / ".arc/preflight.json").write_text("{}")
        (self.out / ".arc/traceability/x.json").write_text("{}")
        ws = self.make()
        ws.save_json("run.json", {"a": 1})
        ws.init(self.req, [])  # a rerun into the same folder
        for rel in [".arc/runner-events.jsonl", ".arc/preflight.json", ".arc/traceability/x.json", ".arc/arcflash.json"]:
            self.assertTrue((self.out / rel).exists(), rel)
        self.assertFalse((self.out / ".arc/run.json").exists())

    def test_workspace_path_with_spaces(self):
        ws = self.make(self.tmp / "out dir with spaces")
        ws.write({"backend/app.js": "x\n"})
        self.assertEqual(ws.read_generated()["backend/app.js"], "x\n")

    def test_relative_output_path_becomes_absolute(self):
        # P1 used "-o outputs/lite-1": subprocesses run in other folders, so every path must be absolute.
        with contextlib.chdir(self.tmp):
            ws = Workspace(Path("rel out"))
            ws.init(self.req, [])
        self.assertTrue(ws.out.is_absolute()); self.assertEqual(ws.out, (self.tmp / "rel out").resolve())
        for path in (ws.arc, ws.harness, ws.check_dir, ws.calls_dir, ws.versions):
            self.assertTrue(path.is_absolute(), path)
        self.assertTrue((self.tmp / "rel out" / ".arc/harness/tests/helpers.ts").exists())

    def test_tests_are_written_into_the_harness(self):
        ws = self.make()
        ws.write({"backend/app.js": "x\n", "tests/scenarios-1.spec.ts": "y\n"})
        self.assertEqual((self.out / ".arc/harness/tests/scenarios-1.spec.ts").read_text(), "y\n")
        self.assertEqual(ws.read_generated()["tests/scenarios-1.spec.ts"], "y\n")

    def test_read_generated_lists_only_generated_files(self):
        self.assertEqual(set(self.make().read_generated()),
                         {"backend/app.js", "frontend/src/shared.jsx", "frontend/src/App.jsx", "frontend/src/pages/main.jsx"})

    def test_write_refuses_other_paths(self):
        with self.assertRaises(WorkspaceError): self.make().write({"deploy.sh": "x\n"})

    def test_snapshot_restore_round_trip_removes_extra_files(self):
        ws = self.make()
        ws.write({"backend/app.js": "v1\n", "tests/scenarios-1.spec.ts": "t1\n"})
        snap = ws.snapshot("a1-r0", {"rank": [1]})
        self.assertEqual(json.loads((snap / "meta.json").read_text()), {"rank": [1]})
        ws.write({"backend/app.js": "v2\n", "frontend/src/pages/extra.jsx": "e\n", "tests/scenarios-2.spec.ts": "t2\n"})
        ws.restore(snap)
        self.assertEqual((self.out / "backend/app.js").read_text(), "v1\n")
        self.assertFalse((self.out / "frontend/src/pages/extra.jsx").exists())
        self.assertFalse((self.out / ".arc/harness/tests/scenarios-2.spec.ts").exists())
        self.assertEqual(ws.read_generated()["tests/scenarios-1.spec.ts"], "t1\n")

    def test_reset_to_template(self):
        ws = self.make()
        ws.write({"frontend/src/App.jsx": "changed\n", "frontend/src/pages/extra.jsx": "e\n", "tests/scenarios-1.spec.ts": "t\n"})
        ws.reset_to_template()
        self.assertEqual((self.out / "frontend/src/App.jsx").read_text(), (TEMPLATE_APP / "frontend/src/App.jsx").read_text())
        self.assertFalse((self.out / "frontend/src/pages/extra.jsx").exists())
        self.assertFalse((self.out / ".arc/harness/tests/scenarios-1.spec.ts").exists())
        self.assertTrue((self.out / ".arc/harness/tests/helpers.ts").exists())

    def test_ship_removes_test_data_and_harness_modules_but_keeps_runner_files(self):
        ws = self.make()
        for rel in ["backend/data/app.db", ".arc/check/test.db", ".arc/harness/node_modules/x", ".arc/runner-events.jsonl"]:
            (self.out / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.out / rel).write_text("x")
        ws.ship()
        self.assertFalse((self.out / "backend/data").exists()); self.assertFalse((self.out / ".arc/harness/node_modules").exists())
        self.assertFalse((self.out / ".arc/check").exists())
        self.assertTrue((self.out / ".arc/runner-events.jsonl").exists()); self.assertTrue((self.out / "backend/app.js").exists())

    def test_save_json_and_text(self):
        ws = self.make()
        ws.save_json("plan-1.json", {"x": "ü"}); ws.save_text("digest.txt", "d")
        self.assertEqual(json.loads((self.out / ".arc/plan-1.json").read_text(encoding="utf-8")), {"x": "ü"})
        self.assertEqual((self.out / ".arc/digest.txt").read_text(), "d")

    def test_template_is_javascript_with_exact_versions(self):
        self.assertEqual([p for p in TEMPLATE_APP.rglob("*") if p.suffix in {".ts", ".tsx"}], [])
        manifests = list(TEMPLATE_APP.rglob("package.json")) + [TEMPLATE / "harness" / "package.json"]
        self.assertEqual(len(manifests), 4)
        for path in manifests:
            data = json.loads(path.read_text())
            for name, version in {**data.get("dependencies", {}), **data.get("devDependencies", {})}.items():
                self.assertRegex(version, r"^\d+\.\d+\.\d+$", f"{path}: {name}")

    def test_local_env_bypasses_proxies(self):
        env = proc.local_env()
        for key in ("NO_PROXY", "no_proxy"): self.assertIn("127.0.0.1", env[key]); self.assertIn("localhost", env[key])
        self.assertEqual(proc.local_env({"X_TEST": "1"})["X_TEST"], "1")

    def test_run_merges_output_and_times_out(self):
        r = proc.run(["sh", "-c", "echo out; echo err >&2"], self.tmp, timeout=10)
        self.assertEqual(r.returncode, 0); self.assertIn("out", r.output); self.assertIn("err", r.output)
        r = proc.run(["sh", "-c", "sleep 5"], self.tmp, timeout=0.5)
        self.assertTrue(r.timed_out); self.assertNotEqual(r.returncode, 0)

    def test_wait_for_http_gives_up_when_the_process_exits(self):
        p = proc.start(["sh", "-c", "exit 3"], self.tmp, env=proc.local_env(), log_path=self.tmp / "log.txt")
        self.assertFalse(proc.wait_for_http(f"http://127.0.0.1:{proc.free_port()}/", 10, p))
        proc.stop(p)

    def test_tail(self):
        self.assertEqual(proc.tail("\n".join(str(i) for i in range(100)), 3), "97\n98\n99")


if __name__ == "__main__":
    unittest.main()
