"""Text utility functions."""


def highlight_span(text: str, start: int, end: int, color: str = "#fef08a") -> str:
    """Insert HTML highlight span around a text range."""
    if start is None or end is None or start < 0 or end > len(text):
        return text
    before = text[:start]
    span = text[start:end]
    after = text[end:]
    return f'{before}<span style="background-color: {color}; padding: 1px 3px; border-radius: 3px;">{span}</span>{after}'
