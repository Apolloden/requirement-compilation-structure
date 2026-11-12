"""The plan: one JSON object that fixes tables, API, layout, pages and modules before any code is written."""
from __future__ import annotations


def page_component(page_id: str) -> str:
    """Component name of a page: "item_detail" → "PageItemDetail"."""
    return "Page" + "".join(part[:1].upper() + part[1:] for part in page_id.split("_") if part)
