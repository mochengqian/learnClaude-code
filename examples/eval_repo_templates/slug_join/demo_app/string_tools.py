from __future__ import annotations


def slugify_title(title: str) -> str:
    parts = [piece.strip().lower() for piece in title.split() if piece.strip()]
    return "_".join(parts)
