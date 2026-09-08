"""Peek at IMAP From headers without downloading message bodies."""

from collections.abc import Sequence
from email import message_from_bytes
from typing import Any

from onyx.connectors.imap.allowed_senders import AllowedSender, sender_is_allowed

_IMAP_OKAY_STATUS = "OK"
_FROM_HEADER_PEEK = "(BODY.PEEK[HEADER.FIELDS (FROM)])"


def fetch_from_header(mail_client: Any, email_id: str) -> str | None:
    """Return the From header for one message, or None if it cannot be read.

    Uses BODY.PEEK so the server does not mark the message as seen and does not
    send the body.
    """
    status, msg_data = mail_client.fetch(
        message_set=email_id,
        message_parts=_FROM_HEADER_PEEK,
    )
    if status != _IMAP_OKAY_STATUS or not msg_data:
        return None
    raw = payload_bytes_from_fetch(msg_data)
    if not raw:
        return None
    parsed = message_from_bytes(raw)
    from_header = parsed.get("From")
    if not from_header or not str(from_header).strip():
        return None
    return str(from_header)


def payload_bytes_from_fetch(msg_data: list[Any]) -> bytes | None:
    """First FETCH payload bytes, ignoring the closing parenthesis item."""
    for item in msg_data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def email_ids_allowed_for_body_fetch(
    mail_client: Any,
    email_ids: Sequence[str],
    allow_list: Sequence[AllowedSender],
) -> list[str]:
    """IDs whose body may be FETCHed. Empty allow-list skips the header peek."""
    if not allow_list:
        return [email_id for email_id in email_ids if email_id]
    allowed: list[str] = []
    for email_id in email_ids:
        if not email_id:
            continue
        from_header = fetch_from_header(mail_client, email_id)
        if from_header is None or not sender_is_allowed(from_header, allow_list):
            continue
        allowed.append(email_id)
    return allowed
