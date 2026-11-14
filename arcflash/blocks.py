"""File blocks and SEARCH/REPLACE edits in model answers (Appendix B.3)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from arcflash import config

ALLOWED_PATH = re.compile(r"backend/app\.js|frontend/src/shared\.jsx|frontend/src/App\.jsx"
                          r"|frontend/src/pages/[a-z][a-z0-9_]*\.jsx|tests/scenarios-[0-9]+\.spec\.ts")
HEADER = re.compile(r"^=== (FILE|EDIT): (.+?) ===\s*$")
END = {"FILE": re.compile(r"^=== END FILE ===\s*$"), "EDIT": re.compile(r"^=== END EDIT ===\s*$")}
PAIR = re.compile(r"^<{7} SEARCH[ \t]*\n(.*?)\n={7}[ \t]*\n(.*?)\n>{7} REPLACE[ \t]*$", re.S | re.M)
SEARCH, DIVIDER, REPLACE = re.compile(r"^<{7} SEARCH\s*$"), re.compile(r"^={7}\s*$"), re.compile(r"^>{7} REPLACE\s*$")


@dataclass
class Edit:
    path: str
    search: str | None     # None = replace the whole file
    replace: str


def is_allowed_path(path: str) -> bool:
    """Only the generated file keys may be written by the model."""
    return bool(ALLOWED_PATH.fullmatch(path))


def _blocks(text: str) -> list[tuple[str, str, list[str]]]:
    """Blocks as (kind, path, lines), in answer order. A FILE block without its end line is dropped (half a file is worse
    than none); an EDIT block without it is closed by the next header or the end of the text, and the pair parser keeps
    only its complete SEARCH/REPLACE pairs (v4: two repair rounds lost every edit to missing END lines)."""
    found, current = [], None
    for line in text.splitlines():
        if current is not None and END[current[0]].match(line):
            found.append(current)
            current = None
            continue
        header = HEADER.match(line)
        if header and not line.startswith("=== END "):
            if current is not None and current[0] == "EDIT":
                found.append(current)
            current = (header.group(1), header.group(2).strip().strip("`").strip(), [])
        elif current is not None:
            current[2].append(line)
    if current is not None and current[0] == "EDIT":
        found.append(current)
    return found


def _unfence(lines: list[str]) -> list[str]:
    """Remove one Markdown fence around the whole block content."""
    first = next((i for i, l in enumerate(lines) if l.strip()), None)
    last = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
    if first is not None and last is not None and first < last \
            and lines[first].strip().startswith("```") and lines[last].strip() == "```":
        return lines[first + 1:last]
    return lines


def _content(lines: list[str]) -> str:
    return "\n".join(lines).rstrip("\n") + "\n"


def _join(lines: list[str]) -> str:
    return "".join(line + "\n" for line in lines)


def parse_file_blocks(text: str) -> dict[str, str]:
    return {path: _content(_unfence(lines)) for kind, path, lines in _blocks(text) if kind == "FILE"}


class RepeatGuard:
    """Says when a streaming repair answer has started repeating itself: REPEAT_GUARD_PAIRS pairs in a row that change
    nothing (SEARCH equals REPLACE) or repeat an earlier pair. A real pair resets the run (evidence.md 2.5, A.2)."""

    def __call__(self, text: str) -> bool:
        seen, run = set(), 0
        for m in PAIR.finditer(text):
            pair = (m.group(1).strip(), m.group(2).strip())
            run = run + 1 if pair[0] == pair[1] or pair in seen else 0
            seen.add(pair)
            if run >= config.REPEAT_GUARD_PAIRS:
                return True
        return False


def parse_edits(text: str) -> list[Edit]:
    """EDIT blocks (one Edit per SEARCH/REPLACE pair) and FILE blocks (whole-file Edits), in answer order. A pair that
    changes nothing or repeats an earlier pair of the answer is skipped: applied twice, it would fail and cost a call."""
    edits, seen = [], set()
    for kind, path, lines in _blocks(text):
        lines = _unfence(lines)
        if kind == "FILE":
            edits.append(Edit(path, None, _content(lines)))
            continue
        state, search, replace = None, [], []
        for line in lines:
            if SEARCH.match(line):
                state, search, replace = "search", [], []
            elif state == "search" and DIVIDER.match(line):
                state = "replace"
            elif state == "replace" and REPLACE.match(line):
                key = (path, _join(search), _join(replace))
                if key[1].strip() != key[2].strip() and key not in seen:
                    edits.append(Edit(*key))
                seen.add(key)
                state = None
            elif state == "search":
                search.append(line)
            elif state == "replace":
                replace.append(line)
    return edits


def _line_matches(content: str, search: str) -> list[tuple[int, int]]:
    """Places where the SEARCH lines match whole lines, ignoring trailing spaces: (first line, line count)."""
    lines = content.splitlines()
    wanted = [l.rstrip() for l in search.splitlines()]
    if not wanted:
        return []
    return [(i, len(wanted)) for i in range(len(lines) - len(wanted) + 1)
            if all(lines[i + j].rstrip() == wanted[j] for j in range(len(wanted)))]


def apply_edits(files: dict[str, str], edits: list[Edit]) -> tuple[dict[str, str], list[str]]:
    """Apply edits in order to a copy of files. A failed edit changes nothing and adds one problem line."""
    result, problems = dict(files), []
    for edit in edits:
        if not is_allowed_path(edit.path):
            problems.append(f"{edit.path}: path not allowed")
            continue
        if edit.search is None:
            result[edit.path] = edit.replace
            continue
        if edit.path not in result:
            problems.append(f"{edit.path}: file does not exist; send a file block")
            continue
        content = result[edit.path]
        count = content.count(edit.search) if edit.search else 0
        if count == 1:
            result[edit.path] = content.replace(edit.search, edit.replace, 1)
            continue
        if count == 0:
            places = _line_matches(content, edit.search)
            if len(places) == 1:
                start, size = places[0]
                lines = content.splitlines(keepends=True)
                result[edit.path] = "".join(lines[:start]) + edit.replace + "".join(lines[start + size:])
                continue
            count = len(places)
        if count > 1:
            problems.append(f"{edit.path}: SEARCH text matches {count} places; add lines to make it unique")
        else:
            first = next((l.strip() for l in edit.search.splitlines() if l.strip()), "")
            problems.append(f"{edit.path}: SEARCH text not found: {first}")
    return result, problems


def render_files(files: dict[str, str]) -> str:
    """Current files for a prompt: "=== <path> ===" then the content."""
    return "".join(f"=== {path} ===\n{c if c.endswith(chr(10)) else c + chr(10)}" for path, c in files.items())
