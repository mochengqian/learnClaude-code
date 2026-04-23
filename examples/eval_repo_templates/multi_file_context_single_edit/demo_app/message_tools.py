from __future__ import annotations

from demo_app.format_rules import DEFAULT_SUFFIX


def render_message(name: str) -> str:
    normalized = name.strip().title()
    return "Hello, {0}{1}".format(normalized, ".")
