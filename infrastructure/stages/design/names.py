"""The role and name of a design element, as a browser test would look it up.

A test written from the requirement text finds a control by its role and its visible name
(for example the link "Home" or the text box named by its placeholder). These helpers read
the same two facts from a design element so the design can be checked against them."""

ROLES = ("link", "button", "heading", "textbox", "combobox", "checkbox", "region", "radiogroup")
TEXTBOX_INPUTS = {"text", "textarea", "email", "tel", "url"}
SIMPLE_ROLES = {"link": "link", "button": "button", "heading": "heading", "select": "combobox",
    "checkbox": "checkbox"}


def element_name(node: dict) -> str | None:
    """The fixed visible name of an element, or None when it has none or it is filled in at runtime."""
    prop = node.get("prop") or {}
    kind = node.get("type")
    if kind == "heading":
        value = prop.get("text")
    elif kind == "input":
        # A label names the field; without one the browser falls back to the placeholder.
        value = prop.get("label") if prop.get("label") not in (None, "") else prop.get("placeholder")
    else:
        value = prop.get("label")
    if not isinstance(value, str) or not value or "{" in value:
        return None
    return value


def element_role(node: dict, region_names=frozenset(), radio_group: bool = False) -> str | None:
    """The role a browser test would use for the element, or None when it has no such role.

    radio_group: the element directly holds radio inputs; with a name it is a radiogroup."""
    kind = node.get("type")
    if kind in SIMPLE_ROLES:
        return SIMPLE_ROLES[kind]
    if kind == "input":
        input_type = (node.get("prop") or {}).get("input_type")
        return "textbox" if input_type is None or input_type in TEXTBOX_INPUTS else None
    if kind in ("container", "list"):
        if radio_group and element_name(node):
            return "radiogroup"
        # A region only counts when the naming table names it; most containers are layout only.
        return "region" if element_name(node) in region_names else None
    return None


def is_password_field(node: dict) -> bool:
    """A password input: named like a field, but a browser gives it no textbox role."""
    return node.get("type") == "input" and (node.get("prop") or {}).get("input_type") == "password"


def page_subtree(design: dict, pid: str) -> list[str]:
    """IDs of the elements under a page's root (the root included), in design order."""
    elements = design.get("elements", {})
    root = design.get("pages", {}).get(pid, {}).get("root")
    found = []
    for uid in elements:
        current, seen = uid, set()
        while current in elements and current not in seen:
            if current == root:
                found.append(uid)
                break
            seen.add(current)
            current = elements[current].get("parent")
    return found


def named_elements(design: dict, pid: str, plan: dict | None = None) -> list[tuple[str, str, str]]:
    """(element ID, role, name) for every element on the page that has both a role and a fixed name."""
    region_names = {entry.get("name") for entry in (plan or {}).get("naming", [])
        if isinstance(entry, dict) and entry.get("role") == "region"}
    radio_parents = {node.get("parent") for node in design.get("elements", {}).values()
        if node.get("type") == "input" and (node.get("prop") or {}).get("input_type") == "radio"}
    found = []
    for uid in page_subtree(design, pid):
        node = design["elements"][uid]
        role, name = element_role(node, region_names, uid in radio_parents), element_name(node)
        if role and name:
            found.append((uid, role, name))
    return found
