"""Resolve the folder list stored on one blob connector.

`prefixes` is the new stored field. The legacy single `prefix` is still
read so connectors created before the list keep working unchanged.
"""


def normalize_blob_prefix(prefix: str) -> str:
    """Match the historical single-prefix rule: append '/' unless empty or already present."""
    return prefix if not prefix or prefix.endswith("/") else prefix + "/"


def resolve_blob_prefixes(prefixes: list[str] | None, prefix: str) -> list[str]:
    """Folder list for one connector.

    `prefixes` wins when provided. An empty or blank list is rejected (no
    whole-bucket option). When `prefixes` is omitted, the legacy `prefix`
    field is used unchanged — including empty, so connectors created before
    this field keep working.

    Raises:
        ValueError: `prefixes` is present but not a non-empty list of strings.
    """
    if prefixes is None:
        return [normalize_blob_prefix(prefix)]
    if not isinstance(prefixes, list):
        raise ValueError("S3 prefixes must be a list of folder paths.")
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in prefixes:
        if not isinstance(item, str):
            raise ValueError("S3 prefixes must be a list of folder paths.")
        text = item.strip()
        if not text:
            continue
        normalized = normalize_blob_prefix(text)
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(normalized)
    if not cleaned:
        raise ValueError(
            "S3 needs at least one folder. The whole bucket cannot be selected."
        )
    return cleaned
