"""Allow-list matching for the IMAP connector.

Empty list means no sender filter. A non-empty list is fail-closed: only matching
From addresses are indexed. SEARCH FROM is a coarse first pass; exact matching
uses the parsed address after a header peek.
"""

from collections.abc import Sequence
from datetime import datetime, timezone
from email.utils import parseaddr
from typing import Literal, NamedTuple

ALLOWED_SENDERS_MAX = 50
_MAX_ENTRY_LEN = 254
_FORBIDDEN_CHARS = frozenset({'"', "\\", "\r", "\n", "{", "}", "\x00"})


class AllowedSendersError(ValueError):
    """Raised when an allow-list entry cannot be stored as IMAP SEARCH text."""


class AllowedSender(NamedTuple):
    kind: Literal["address", "domain"]
    value: str


def parse_allowed_senders(raw: list[str] | None) -> tuple[AllowedSender, ...]:
    """Parse wizard/config entries into addresses and domains.

    Blank or missing input is an empty allow-list (no sender filter). Invalid
    characters, non-ASCII, or more than ALLOWED_SENDERS_MAX entries raise
    AllowedSendersError so a bad list cannot silently become "allow everyone."
    """
    if not raw:
        return ()
    entries = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    if not entries:
        return ()
    if len(entries) > ALLOWED_SENDERS_MAX:
        raise AllowedSendersError(
            f"At most {ALLOWED_SENDERS_MAX} approved senders or domains are allowed"
        )
    parsed: list[AllowedSender] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        rule = _parse_one_allowed_sender(entry)
        key = (rule.kind, rule.value)
        if key in seen:
            continue
        seen.add(key)
        parsed.append(rule)
    return tuple(parsed)


def sender_is_allowed(from_header: str, allow_list: Sequence[AllowedSender]) -> bool:
    """True when From matches the allow-list, or the list is empty.

    Unparseable From headers are rejected (fail closed). Domain rules match the
    host and subdomains. Address rules are exact, case-insensitive.
    """
    if not allow_list:
        return True
    addr = parse_from_address(from_header)
    if not addr or "@" not in addr:
        return False
    host = addr.rsplit("@", 1)[1]
    for rule in allow_list:
        if rule.kind == "address" and addr == rule.value:
            return True
        if rule.kind == "domain" and _host_matches_domain(host, rule.value):
            return True
    return False


def parse_from_address(from_header: str) -> str | None:
    """Return the lowercase mailbox from a From header, or None if missing."""
    if not from_header or not from_header.strip():
        return None
    _name, addr = parseaddr(from_header)
    addr = addr.strip().lower()
    return addr if addr and "@" in addr else None


def build_mailbox_search_criteria(
    start: float,
    end: float,
    allow_list: Sequence[AllowedSender],
) -> str:
    """IMAP SEARCH for the date window, plus OR FROM keys when an allow-list is set."""
    date_part = _date_window_criteria(start, end)
    from_part = _from_or_criteria(allow_list)
    if not from_part:
        return f"({date_part})"
    return f"({date_part} {from_part})"


def _parse_one_allowed_sender(entry: str) -> AllowedSender:
    if len(entry) > _MAX_ENTRY_LEN:
        raise AllowedSendersError("Each approved sender or domain is too long")
    if any(char in _FORBIDDEN_CHARS for char in entry):
        raise AllowedSendersError("Approved senders cannot include quotes or IMAP special characters")
    try:
        entry.encode("ascii")
    except UnicodeEncodeError as exc:
        raise AllowedSendersError("Approved senders must be ASCII") from exc
    if entry.startswith("@") and entry.count("@") == 1:
        return AllowedSender("domain", _normalize_domain(entry[1:]))
    if "@" in entry:
        return AllowedSender("address", _normalize_address(entry))
    return AllowedSender("domain", _normalize_domain(entry))


def _normalize_address(entry: str) -> str:
    if entry.count("@") != 1:
        raise AllowedSendersError("Each approved address needs exactly one @")
    local, host = entry.split("@", 1)
    if not local.strip() or not host.strip():
        raise AllowedSendersError("Approved addresses need a local part and a domain")
    return f"{local.strip().lower()}@{_normalize_domain(host)}"


def _normalize_domain(entry: str) -> str:
    domain = entry.strip().lower().lstrip(".")
    if not domain or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise AllowedSendersError("Approved domains must look like company.com")
    if " " in domain or "@" in domain:
        raise AllowedSendersError("Approved domains cannot contain spaces or @")
    return domain


def _host_matches_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _date_window_criteria(start: float, end: float) -> str:
    start_str = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%d-%b-%Y")
    end_str = datetime.fromtimestamp(end, tz=timezone.utc).strftime("%d-%b-%Y")
    return f'SINCE "{start_str}" BEFORE "{end_str}"'


def _from_or_criteria(allow_list: Sequence[AllowedSender]) -> str:
    if not allow_list:
        return ""
    keys = [f'FROM "{rule.value}"' for rule in allow_list]
    expr = keys[-1]
    for part in reversed(keys[:-1]):
        expr = f"OR {part} {expr}"
    return expr
