"""Shared speaker prefix for folded multi-author document text.

Connectors resolve a display name (never email) then call this. Empty or
missing names become ``Unknown`` so every message still has a prefix.
"""


def format_attributed_message(name: str | None, text: str) -> str:
    """Format one message, comment, or post inside a folded document.

    Args:
        name: Already-resolved display name. None or blank becomes Unknown.
        text: Message body. Not stripped; callers own whitespace.

    Returns:
        ``"{speaker}: {text}"`` with speaker never empty.

    Raises:
        None.
    """
    speaker = (name or "").strip() or "Unknown"
    return f"{speaker}: {text}"
