import json
import shutil
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path

from arcflash import proc
from arcflash.workspace import Workspace
from tests.support import FIXTURES, integration

NOTES = FIXTURES / "specs" / "notes"


def get(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


@integration
class TemplateIntegrationTests(unittest.TestCase):
    def test_template_app_installs_builds_and_starts(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        ws = Workspace(tmp / "template run")
        ws.init(NOTES, [])
        install = ws.install_app()
        self.assertEqual(install.returncode, 0, install.output[-3000:])
        build = proc.run([proc.npm(), "run", "build"], ws.out, timeout=300)
        self.assertEqual(build.returncode, 0, build.output[-3000:])
        port = proc.free_port()
        ws.check_dir.mkdir(parents=True, exist_ok=True)
        env = proc.local_env({"PORT": str(port), "DATABASE_URL": str(ws.check_dir / "test.db")})
        server = proc.start(["node", "backend/server.js"], ws.out, env=env, log_path=ws.check_dir / "server.log")
        try:
            base = f"http://127.0.0.1:{port}"
            self.assertTrue(proc.wait_for_http(base + "/api/health", 30, server), (ws.check_dir / "server.log").read_text())
            self.assertEqual(json.loads(get(base + "/api/health")[1]), {"status": "ok"})
            for path in ("/", "/deep/link"):
                status, body = get(base + path)
                self.assertEqual(status, 200); self.assertIn('<div id="root">', body)
            self.assertEqual(get(base + "/api/nothing")[0], 404)
        finally:
            proc.stop(server)
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=2).close()
        self.assertFalse((ws.out / "backend/data").exists())  # DATABASE_URL was honoured


if __name__ == "__main__":
    unittest.main()
