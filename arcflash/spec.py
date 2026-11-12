"""Requirements folder → requirement tree, compact digest (Appendix B.1), role names, images and assets. No model."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

NOTE_CHARS = 20_000
ROLE_NAME = re.compile(r'["“]([^"”\n]{1,120})["”]\s*\(role=([a-z]+)\)')
REF = re.compile(r"\\ref\[([a-z]+)\]\(([^)]*)\)")


@dataclass
class ImageRef:
    ids: list[str]
    path: str          # relative to the folder, e.g. "reference/index.png"
    page: str


@dataclass
class Step:
    id: str
    given: str
    when: str
    then: str
    uses: list[str]


@dataclass
class Scenario:
    id: str
    name: str
    requirement: str
    steps: list[Step]


@dataclass
class Requirement:
    id: str
    name: str
    text: str
    parent: str | None
    children: list[str]
    dependencies: list[str]
    image_ids: list[str]
    scenarios: list[Scenario]
    images: list[tuple[str, str]] = field(default_factory=list)   # (image id, page), for the digest

    @property
    def is_leaf(self) -> bool:
        return not self.children


@dataclass
class Spec:
    title: str
    text: str
    requirements: dict[str, Requirement]       # file order
    leaves: list[str]
    scenarios: list[Scenario]                  # file order
    images: list[ImageRef]                     # one per distinct path, in first-reference order
    assets: list[str]                          # paths under assets/, sorted
    notes: str                                 # extra .md/.txt files, "" when none
    role_names: list[tuple[str, str]]          # (role, name) from "Name"(role=x), no names containing "{"
    corpus: str                                # every text in the document, for exact-name lookups
    digest: str                                # Appendix B.1


class SpecError(ValueError):
    pass


def _clean(text) -> str:
    """Text as written, without \\ref markup, with trailing spaces removed from every line."""
    if text is None:
        return ""
    text = REF.sub(lambda m: m.group(2), str(text))
    return "\n".join(line.rstrip() for line in text.strip("\n").split("\n")).strip()


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class _Reader:
    def __init__(self) -> None:
        self.requirements: dict[str, Requirement] = {}
        self.images: dict[str, ImageRef] = {}
        self.texts: list[str] = []

    def new_id(self) -> str:
        n = len(self.requirements) + 1
        while f"REQ-{n}" in self.requirements:
            n += 1
        return f"REQ-{n}"

    def read(self, node: dict, parent: str | None) -> str:
        if not isinstance(node, dict):
            raise SpecError(f"a requirement must be a mapping, got {type(node).__name__}")
        rid = str(node.get("id") or self.new_id())
        description = node.get("description")
        if isinstance(description, dict):
            text, image_entries = description.get("text"), _as_list(description.get("image"))
        else:
            text, image_entries = description, []
        req = Requirement(id=rid, name=_clean(node.get("name")), text=_clean(text), parent=parent, children=[],
                          dependencies=[str(d) for d in _as_list(node.get("dependencies"))], image_ids=[], scenarios=[])
        self.requirements[rid] = req
        self.texts += [req.name, req.text]
        for entry in image_entries:
            if not isinstance(entry, dict) or not entry.get("reference"):
                continue
            image_id, path, page = str(entry.get("id") or entry["reference"]), str(entry["reference"]), str(entry.get("page") or "")
            if image_id not in req.image_ids:
                req.image_ids.append(image_id)
                req.images.append((image_id, page))
            ref = self.images.setdefault(path, ImageRef(ids=[], path=path, page=page))
            if image_id not in ref.ids:
                ref.ids.append(image_id)
        for n, raw in enumerate(_as_list(node.get("scenarios")), start=1):
            req.scenarios.append(self.scenario(raw if isinstance(raw, dict) else {}, rid, n))
        for child in _as_list(node.get("children")):
            req.children.append(self.read(child, rid))
        return rid

    def scenario(self, raw: dict, rid: str, n: int) -> Scenario:
        sid = str(raw.get("id") or f"SCN-{rid}-{n:02d}")
        steps = []
        for k, step in enumerate(_as_list(raw.get("steps")), start=1):
            step = step if isinstance(step, dict) else {}
            uses = []
            for ref in _as_list(step.get("references")):
                m = REF.search(str(ref))
                if m and m.group(1) == "requirement" and m.group(2) not in uses:
                    uses.append(m.group(2))
            s = Step(id=str(step.get("id") or f"STEP-{sid}-{k:02d}"), given=_clean(step.get("Given")),
                     when=_clean(step.get("When")), then=_clean(step.get("Then")), uses=uses)
            self.texts += [s.given, s.when, s.then]
            steps.append(s)
        scenario = Scenario(id=sid, name=_clean(raw.get("name")), requirement=rid, steps=steps)
        self.texts.append(scenario.name)
        return scenario


def _read_notes(folder: Path) -> str:
    parts = []
    for path in sorted(p for p in folder.iterdir() if p.is_file() and p.suffix in {".md", ".txt"}):
        if path.name == "requirements.md":
            continue
        text = _clean(path.read_text(encoding="utf-8", errors="replace")[:NOTE_CHARS])
        if text:
            parts.append(f"From {path.name}:\n{text}")
    return "\n\n".join(parts)


def _read_assets(folder: Path) -> list[str]:
    root = folder / "assets"
    if not root.is_dir():
        return []
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts))


def _render_scenarios(req: Requirement) -> list[str]:
    lines = []
    for sc in req.scenarios:
        lines.append(f"Scenario {sc.id}: {sc.name}")
        for st in sc.steps:
            lines += [f"  Step {st.id}", f"    Given: {st.given}", f"    When: {st.when}", f"    Then: {st.then}"]
            if st.uses:
                lines.append(f"    Uses: {', '.join(st.uses)}")
    return lines


def _render_requirement(req: Requirement) -> str:
    lines = [f"GROUP {req.id}: {req.name}" if req.children else f"{req.id}: {req.name}"]
    if req.dependencies:
        lines.append(f"Depends on: {', '.join(req.dependencies)}")
    if req.images:
        lines.append("Screenshots: " + ", ".join(f"{i} (page {p})" if p else i for i, p in req.images))
    if req.text:
        lines.append(req.text)
    return "\n".join(lines + _render_scenarios(req))


def load_spec(folder: Path) -> Spec:
    folder = Path(folder)
    path = folder / "requirements.yaml"
    if not path.is_file():
        raise SpecError(f"no requirements.yaml in {folder}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise SpecError(f"requirements.yaml is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise SpecError("requirements.yaml must hold one requirement mapping at the top")

    reader = _Reader()
    root_id = reader.read(data, None)
    root = reader.requirements[root_id]
    requirements = reader.requirements
    leaves = [rid for rid, r in requirements.items() if r.is_leaf]
    scenarios = [sc for r in requirements.values() for sc in r.scenarios]
    notes = _read_notes(folder)
    assets = _read_assets(folder)
    corpus = "\n".join(t for t in reader.texts + [notes] if t)

    role_names: list[tuple[str, str]] = []
    for name, role in ROLE_NAME.findall(corpus):
        if "{" not in name and (role, name) not in role_names:
            role_names.append((role, name))

    root_scenarios = [] if root.is_leaf else _render_scenarios(root)   # a root leaf is rendered under REQUIREMENTS
    blocks = ["\n".join([f"APPLICATION: {root.name}"] + ([root.text] if root.text else []) + root_scenarios)]
    if notes:
        blocks.append("NOTES FROM THE TASK FOLDER\n" + notes)
    if assets:
        blocks.append("ASSETS (served at /assets/<name>): " + ", ".join(assets))
    blocks.append("REQUIREMENTS\nGroups give context; leaf requirements are what you build.")
    blocks += [_render_requirement(r) for rid, r in requirements.items() if rid != root_id or r.is_leaf]
    digest = "\n\n".join(blocks) + "\n"

    return Spec(title=root.name, text=root.text, requirements=requirements, leaves=leaves, scenarios=scenarios,
                images=list(reader.images.values()), assets=assets, notes=notes, role_names=role_names,
                corpus=corpus, digest=digest)
