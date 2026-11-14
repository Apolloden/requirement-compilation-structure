import contextlib
import io
import json
import shutil
import tempfile
import unittest
import urllib.request
from pathlib import Path

from arcflash import proc
from arcflash.pipeline import default_tools, run
from tests.fakes import FakeLLM, as_blocks
from tests.golden import golden_files, golden_plan
from tests.support import FIXTURES, integration

NOTES = FIXTURES / "specs" / "notes"


@integration
class PipelineIntegrationTests(unittest.TestCase):
    def test_end_to_end_with_scripted_answers(self):
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "pipeline run"
        files = golden_files()
        llm = FakeLLM({
            "vision:": ["Layout: a list of notes."],
            "tests:": [as_blocks({"tests/scenarios-1.spec.ts": files["tests/scenarios-1.spec.ts"]})],
            "plan": [json.dumps(golden_plan())],
            "backend": [as_blocks({"backend/app.js": files["backend/app.js"]})],
            "frontend:shell": [as_blocks({k: files[k] for k in ("frontend/src/shared.jsx", "frontend/src/App.jsx")})],
            "frontend:m1": [as_blocks({"frontend/src/pages/m1.jsx": files["frontend/src/pages/m1.jsx"]})],
        })
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            result = run(NOTES, out, llm=llm, tools=default_tools())
        self.assertEqual((result["shipped"]["scenarios_passed"], result["shipped"]["scenarios_total"]), (3, 3), printed.getvalue())
        self.assertEqual((result["shipped"]["source"], result["harness_ok"]), ("a1-r0", True))
        self.assertFalse((out / "backend/data").exists())
        self.assertFalse((out / ".arc/check").exists()); self.assertFalse((out / ".arc/harness/node_modules").exists())
        # then, like deploy.sh: build in the output folder and start the server on PORT with a temporary database
        build = proc.run([proc.npm(), "run", "build"], out, timeout=300)
        self.assertEqual(build.returncode, 0, build.output[-2000:])
        port = proc.free_port()
        env = proc.local_env({"PORT": str(port), "DATABASE_URL": str(tmp / "deploy.db")})
        server = proc.start(["node", "backend/server.js"], out, env=env, log_path=tmp / "server.log")
        try:
            self.assertTrue(proc.wait_for_http(f"http://127.0.0.1:{port}/api/health", 30, server))
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=10) as r:
                self.assertEqual(json.loads(r.read()), {"status": "ok"})
        finally:
            proc.stop(server)


if __name__ == "__main__":
    unittest.main()
