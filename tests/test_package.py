import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.support import AGENT_ROOT

# Never copy the real .env: the copy excludes it, then gets a fake one.
IGNORE = shutil.ignore_patterns(".env", ".venv", ".git", "node_modules", "outputs", ".superpowers", "*.zip", "__pycache__")


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.agent = cls.tmp / "agent copy"
        shutil.copytree(AGENT_ROOT, cls.agent, ignore=IGNORE)
        (cls.agent / ".env").write_text("X=1\n")
        (cls.agent / "arcflash" / ".env").write_text("X=1\n")
        (cls.agent / "outputs" / "run").mkdir(parents=True)
        (cls.agent / "outputs" / "run" / "app.js").write_text("x")
        (cls.agent / "arcflash" / "__pycache__").mkdir(exist_ok=True)
        (cls.agent / "arcflash" / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"x")
        cls.zip_path = cls.tmp / "out" / "arcflash-test.zip"
        cls.result = subprocess.run([sys.executable, "scripts/package.py", "-o", str(cls.zip_path)], cwd=cls.agent,
                                    capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_zip_contains_only_the_agent(self):
        self.assertEqual(self.result.returncode, 0, self.result.stdout + self.result.stderr)
        names = zipfile.ZipFile(self.zip_path).namelist()
        self.assertIn("main.py", names); self.assertIn("requirements.txt", names)
        self.assertIn("arcflash/template/app/deploy.sh", names); self.assertIn("arcflash/prompts/rules.md", names)
        self.assertTrue(all(n in ("main.py", "requirements.txt") or n.startswith("arcflash/") for n in names))
        self.assertFalse(any(n.endswith(".env") or "__pycache__" in n for n in names))
        self.assertIn("entries", self.result.stdout)

    def test_deploy_sh_keeps_its_executable_bit(self):
        info = zipfile.ZipFile(self.zip_path).getinfo("arcflash/template/app/deploy.sh")
        self.assertTrue((info.external_attr >> 16) & 0o111)

    def test_output_inside_the_agent_folder_is_refused(self):
        target = self.agent / "inside.zip"
        r = subprocess.run([sys.executable, "scripts/package.py", "-o", str(target)], cwd=self.agent, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0); self.assertFalse(target.exists())

    def test_grade_local_help(self):
        r = subprocess.run([sys.executable, "scripts/grade_local.py", "--help"], cwd=AGENT_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("--port", r.stdout); self.assertIn("--keep", r.stdout)


if __name__ == "__main__":
    unittest.main()
