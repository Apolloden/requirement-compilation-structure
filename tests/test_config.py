import subprocess
import sys
import unittest

from arcflash import config
from tests.support import AGENT_ROOT, SAFE_ENV


class ConfigTests(unittest.TestCase):
    def test_model_and_thinking(self):
        self.assertEqual(config.MODEL, "deepseek-flash")
        self.assertIs(config.THINKING, False)

    def test_limits(self):
        self.assertEqual((config.MAX_REPAIR_ROUNDS, config.MAX_ATTEMPTS, config.MAX_PARALLEL_CALLS, config.FOCUS_GROUPS), (14, 2, 8, 3))
        self.assertEqual(set(config.MAX_TOKENS), {"vision", "plan", "backend", "frontend", "tests", "repair", "quick_fix"})
        self.assertEqual(config.RETRY_WAITS, (15, 30, 60, 120))
        # ArcZenith: worst case 225 + a request in flight (20 min) + a check (about 25 min) = about 271 min; the repairs
        # run until the time budget (ArcLaser v3 graded: Ctrip 83 → 129 own tests in the continued repairs)
        self.assertEqual((config.HARD_STOP_SECONDS, config.MAX_RUN_SECONDS, config.RESCUE_UNTIL_SECONDS), (225 * 60, 200 * 60, 215 * 60))
        self.assertIn("radiogroup", config.ARIA_ROLES)

    def test_main_help_exits_zero(self):
        r = subprocess.run([sys.executable, "main.py", "--help"], cwd=AGENT_ROOT, env=SAFE_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--output-dir", r.stdout)


if __name__ == "__main__":
    unittest.main()
