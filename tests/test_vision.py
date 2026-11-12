import shutil
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from arcflash import config
from arcflash.llm import LLMError
from arcflash.prompting import load_prompt
from arcflash.spec import load_spec
from arcflash.vision import compress_image, describe_images
from tests.fakes import FakeLLM
from tests.support import AGENT_ROOT, FIXTURES

SAMPLE = AGENT_ROOT / "input"
NOTES = FIXTURES / "specs" / "notes"


def png_bytes(width, height, mode="RGB"):
    buffer = BytesIO()
    Image.new(mode, (width, height), (10, 20, 30, 0) if mode == "RGBA" else "white").save(buffer, "PNG")
    return buffer.getvalue()


class VisionTests(unittest.TestCase):
    def test_each_image_is_described_once(self):
        llm = FakeLLM({"vision:": ["Layout: x"] * 3}); notes = describe_images(load_spec(SAMPLE), SAMPLE, llm)
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(set(notes), {"reference/homepage.png", "reference/login.png", "reference/register.png"})
        call = next(c for c in llm.calls if c["purpose"] == "vision:homepage.png")
        self.assertEqual((call["method"], call["user"], call["max_tokens"]), ("describe_image", load_prompt("vision"), config.MAX_TOKENS["vision"]))

    def test_failed_or_missing_image_becomes_unavailable(self):
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, True)
        shutil.copytree(SAMPLE, tmp / "task")
        (tmp / "task" / "reference" / "login.png").unlink()
        llm = FakeLLM({"vision:homepage": [LLMError("busy")], "vision:register": ["Layout: form"]})
        notes = describe_images(load_spec(tmp / "task"), tmp / "task", llm)
        self.assertEqual(notes["reference/homepage.png"], "(screenshot unavailable)")
        self.assertEqual(notes["reference/login.png"], "(screenshot unavailable)")
        self.assertEqual(notes["reference/register.png"], "Layout: form")
        self.assertEqual(sorted(c["purpose"] for c in llm.calls), ["vision:homepage.png", "vision:register.png"])

    def test_large_image_is_shrunk(self):
        data, mime = compress_image(png_bytes(3000, 2000))
        self.assertEqual(mime, "image/jpeg"); self.assertEqual(max(Image.open(BytesIO(data)).size), 1600)

    def test_small_transparent_image_is_kept_in_size_on_white(self):
        data, mime = compress_image(png_bytes(40, 30, "RGBA"))
        image = Image.open(BytesIO(data))
        self.assertEqual((mime, image.size, image.mode), ("image/jpeg", (40, 30), "RGB"))
        self.assertGreater(min(image.getpixel((5, 5))), 240)


if __name__ == "__main__":
    unittest.main()
