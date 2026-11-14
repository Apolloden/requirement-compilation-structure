"""Screenshot notes: one short description per distinct image, in parallel; a failure never stops the run."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

from PIL import Image

from arcflash import config
from arcflash.llm import LLMError
from arcflash.prompting import UNAVAILABLE, load_prompt
from arcflash.spec import Spec


def compress_image(data: bytes) -> tuple[bytes, str]:
    """Long side at most IMAGE_MAX_SIDE, JPEG quality 80, transparency flattened on white."""
    image = Image.open(BytesIO(data))
    image.load()
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, "white")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        image = flat
    else:
        image = image.convert("RGB")
    image.thumbnail((config.IMAGE_MAX_SIDE, config.IMAGE_MAX_SIDE))
    out = BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue(), "image/jpeg"


def _describe(path: str, req_dir: Path, llm) -> str:
    file = Path(req_dir) / path
    if not file.is_file():
        return UNAVAILABLE
    try:
        data, mime = compress_image(file.read_bytes())
    except (OSError, ValueError, Image.DecompressionBombError):
        return UNAVAILABLE
    try:
        answer = llm.describe_image(f"vision:{Path(path).name}", load_prompt("vision"), data, mime,
                                    max_tokens=config.MAX_TOKENS["vision"])
    except LLMError:
        return UNAVAILABLE
    return answer.text.strip() or UNAVAILABLE


def describe_images(spec: Spec, req_dir: Path, llm, into: dict | None = None) -> dict[str, str]:
    """Image path → note, one call per distinct image. `into` receives each note as soon as it exists, so a caller that
    stops waiting can use the notes that are done (Task 9)."""
    paths = [image.path for image in spec.images]
    notes = into if into is not None else {}
    if not paths:
        return notes

    def one(path: str) -> None:
        notes[path] = _describe(path, req_dir, llm)

    with ThreadPoolExecutor(max_workers=config.NOTES_PARALLEL_CALLS) as pool:
        list(pool.map(one, paths))
    return {p: notes[p] for p in paths}
