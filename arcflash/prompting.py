"""Prompt files, {{placeholders}}, the shared context (Appendix B.2) and the screenshot-notes block."""
from __future__ import annotations

import json
import re
from pathlib import Path

from arcflash.spec import Spec

PROMPTS = Path(__file__).resolve().parent / "prompts"
PROMPT_NAMES = ("rules", "plan", "plan_fix", "backend", "frontend_first", "frontend_more", "missing_files", "tests",
                "repair", "edit_fix", "vision")
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
UNAVAILABLE = "(screenshot unavailable)"


def load_prompt(name: str) -> str:
    """The exact file text, with one trailing newline removed."""
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def fill(template: str, **values: str) -> str:
    """Replace every {{key}} in one pass; a missing key raises KeyError; other braces are left alone."""
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def notes_block(spec: Spec, notes: dict[str, str]) -> str:
    """The SCREENSHOT NOTES block, one entry per distinct image; "" when the spec has no images."""
    if not spec.images:
        return ""
    entries = ["SCREENSHOT NOTES\nLayout and style only. The requirement text decides controls and names."]
    for image in spec.images:
        head = f"{', '.join(image.ids)}, {image.path}" + (f" (page {image.page})" if image.page else "")
        entries.append(f"{head}\n{(notes.get(image.path) or UNAVAILABLE).strip()}")
    return "\n\n".join(entries)


def build_context(spec: Spec, notes_text: str = "", plan: dict | None = None) -> str:
    """Digest, then screenshot notes, then the plan, joined by one empty line: the same beginning for every call."""
    parts = [spec.digest.rstrip("\n")]
    if notes_text:
        parts.append(notes_text)
    if plan is not None:
        parts.append("PLAN\n" + json.dumps(plan, ensure_ascii=False, indent=1))
    return "\n\n".join(parts)
