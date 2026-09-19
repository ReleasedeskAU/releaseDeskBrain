"""Discord index-tag keys. Lowercase so they match catalog/Ask filters."""

_METADATA_CHANNEL_KEY = "channel"
_METADATA_THREAD_KEY = "thread"


def discord_document_metadata(
    channel_name: str | None,
    thread_name: str | None,
) -> dict[str, str | list[str]]:
    """Index tags for one Discord message.

    Args:
        channel_name: Text channel name, or None.
        thread_name: Thread title, or None.

    Returns:
        Metadata dict with lowercase keys only.
    """
    metadata: dict[str, str | list[str]] = {}
    if channel_name:
        metadata[_METADATA_CHANNEL_KEY] = channel_name
    if thread_name:
        metadata[_METADATA_THREAD_KEY] = thread_name
    return metadata
