"""The golden app: a hand-written notes app that follows Appendix A.1, with its plan and scenario tests."""
import json

from tests.support import FIXTURES

GOLDEN = FIXTURES / "golden"


def golden_plan() -> dict:
    return json.loads((GOLDEN / "plan.json").read_text(encoding="utf-8"))


def golden_files() -> dict[str, str]:
    """Generated file keys → content: the app files under app/ and the test files under tests/."""
    files = {p.relative_to(GOLDEN / "app").as_posix(): p.read_text(encoding="utf-8")
             for p in sorted((GOLDEN / "app").rglob("*")) if p.is_file()}
    files.update({"tests/" + p.name: p.read_text(encoding="utf-8") for p in sorted((GOLDEN / "tests").glob("*.spec.ts"))})
    return files
