import contextlib
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import arcflash.check
from arcflash.check import run_check
from arcflash.harness import prepare_harness
from arcflash.workspace import Workspace
from tests.golden import golden_files, golden_plan
from tests.support import FIXTURES, integration

NOTES = FIXTURES / "specs" / "notes"
MAIN = "frontend/src/pages/m1.jsx"


@integration
class CheckIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.ws = Workspace(cls.tmp / "check run")
        cls.ws.init(NOTES, [])
        cls.ws.write(golden_files())
        install = cls.ws.install_app()
        assert install.returncode == 0, install.output[-3000:]
        status = prepare_harness(cls.ws.out)
        assert status.ok, status.log[-3000:]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def tearDown(self):
        self.ws.write(golden_files())

    def variant(self, key, old, new):
        content = golden_files()[key]
        self.assertIn(old, content)
        self.ws.write({key: content.replace(old, new, 1)})

    def test_golden_app_passes_everything(self):
        r = run_check(self.ws, golden_plan(), harness_ok=True)
        self.assertTrue(r.built and r.booted, r.to_prompt()); self.assertEqual(r.static_issues, [])
        self.assertEqual(sorted(t.title for t in r.smoke), ["smoke /", "smoke /notes", "smoke /notes/new"])
        self.assertEqual((r.scenario_passed, r.scenario_total), (3, 3), r.to_prompt()); self.assertTrue(r.all_green())

    def test_duplicate_layout_link_fails_the_page_check(self):
        self.variant(MAIN, '<h1 className="text-2xl font-bold">Notes</h1>\n      <Link to="/notes/new"',
                     '<h1 className="text-2xl font-bold">Notes</h1>\n      <Link to="/notes">Notes</Link>\n      <Link to="/notes/new"')
        r = run_check(self.ws, golden_plan(), harness_ok=True)
        failed = [t for t in r.smoke if t.status != "passed"]
        self.assertEqual(failed[0].title, "smoke /notes")
        self.assertIn('2 elements with role link and name "Notes" (at most one allowed)', failed[0].error)

    def test_frontend_syntax_error_fails_the_build(self):
        self.variant(MAIN, "export function PageHome() {", "export function PageHome() {{")
        r = run_check(self.ws, golden_plan(), harness_ok=True)
        self.assertFalse(r.built); self.assertIn(Path(MAIN).name, r.build_log); self.assertEqual(r.scenarios, [])   # the log names the broken module

    def test_backend_crash_fails_the_start(self):
        self.variant("backend/app.js", "export function registerApi(app, db) {",
                     'export function registerApi(app, db) {\n  throw new Error("boom at start");')
        r = run_check(self.ws, golden_plan(), harness_ok=True)
        self.assertTrue(r.built); self.assertFalse(r.booted); self.assertIn("boom at start", r.boot_log)

    def test_wrong_field_name_fails_a_scenario_with_an_aria_snapshot(self):
        self.variant(MAIN, '<label htmlFor="note-body">Body</label>', '<label htmlFor="note-body">Content</label>')
        r = run_check(self.ws, golden_plan(), harness_ok=True)
        shutil.copy(self.ws.harness / "results.json", self.tmp / "wrong-field-results.json")
        failed = [t for t in r.scenarios if t.status != "passed"]
        self.assertTrue(failed); self.assertIn('textbox "Content"', failed[0].aria)
        self.assertIn('textbox "Body" (page note_new) does not appear in the frontend code', r.static_issues)

    def test_relative_output_path_checks_in_the_right_places(self):
        # P1 used "-o outputs/lite-1": the harness ran no browser and the check database landed inside the app.
        with contextlib.chdir(self.tmp):
            relative = Path("check run")
            self.assertTrue(prepare_harness(relative).ok)
            r = run_check(Workspace(relative), golden_plan(), harness_ok=True)
        self.assertEqual((r.scenario_passed, r.scenario_total, len(r.smoke)), (3, 3, 3), r.to_prompt())
        self.assertFalse((self.ws.out / "check run").exists())
        self.assertTrue((self.ws.out / ".arc/check/test.db").exists())

    def test_server_is_stopped_after_each_check(self):
        ports = []
        real = arcflash.check.free_port
        with mock.patch.object(arcflash.check, "free_port", side_effect=lambda: ports.append(real()) or ports[-1]):
            run_check(self.ws, golden_plan(), harness_ok=False)
        self.assertEqual(len(ports), 1)
        with self.assertRaises(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", ports[0]), timeout=2)


if __name__ == "__main__":
    unittest.main()
