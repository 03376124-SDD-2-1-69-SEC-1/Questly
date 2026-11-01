"""Helpers for reading what a rendered page shows."""

import re

LOADING_ICON = re.compile(r"<[^>]*\sdata-loading=[^>]*>")


def loading_icons(html: str) -> list[str]:
    """The opening tag of every loading_icon on the page."""
    return LOADING_ICON.findall(html)
