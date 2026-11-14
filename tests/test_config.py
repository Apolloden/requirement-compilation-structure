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
        self.assertEqual((config.MAX_REPAIR_ROUNDS, config.MAX_ATTEMPTS, config.MAX_PARALLEL_CALLS, config.FOCUS_GROUPS), (6, 2, 4, 3))
        self.assertEqual(set(config.MAX_TOKENS), {"vision", "plan", "backend", "frontend", "tests", "repair"})
        self.assertEqual(config.RETRY_WAITS, (15, 30, 60, 120))
        self.assertIn("radiogroup", config.ARIA_ROLES)

    def test_main_help_exits_zero(self):
        r = subprocess.run([sys.executable, "main.py", "--help"], cwd=AGENT_ROOT, env=SAFE_ENV, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--output-dir", r.stdout)


if __name__ == "__main__":
    unittest.main()
