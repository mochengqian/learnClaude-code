def slugify_title(title: str) -> str:
    cleaned = title.strip().lower()
    pieces = [piece for piece in cleaned.split() if piece]
    return "_".join(pieces)
