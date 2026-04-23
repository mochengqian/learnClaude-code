from __future__ import annotations


def format_status_label(status: str) -> str:
    normalized = status.strip().lower().replace(" ", "_")
    return "status:{0}".format(normalized)
