"""IMAP allow-list: SEARCH FROM first pass, exact From check, body never fetched for rejects."""

from unittest.mock import MagicMock

import pytest

from onyx.connectors.imap.allowed_senders import (
    ALLOWED_SENDERS_MAX,
    AllowedSendersError,
    build_mailbox_search_criteria,
    parse_allowed_senders,
    parse_from_address,
    sender_is_allowed,
)
from onyx.connectors.imap.from_header import email_ids_allowed_for_body_fetch

START = 1_704_067_200.0  # 2024-01-01 UTC
END = 1_735_689_600.0  # 2025-01-01 UTC


def test_empty_allow_list_is_no_sender_filter() -> None:
    assert parse_allowed_senders(None) == ()
    assert parse_allowed_senders([]) == ()
    assert parse_allowed_senders(["  ", ""]) == ()
    assert sender_is_allowed("Mom <mom@gmail.com>", ()) is True


def test_parse_addresses_and_domains() -> None:
    rules = parse_allowed_senders(
        ["Jira@Company.COM", "@alerts.company.com", "cab.company.com"]
    )
    assert [(r.kind, r.value) for r in rules] == [
        ("address", "jira@company.com"),
        ("domain", "alerts.company.com"),
        ("domain", "cab.company.com"),
    ]


def test_rejects_imap_breaking_and_oversized_lists() -> None:
    with pytest.raises(AllowedSendersError, match="quotes"):
        parse_allowed_senders(['evil"from'])
    with pytest.raises(AllowedSendersError, match="ASCII"):
        parse_allowed_senders(["café@company.com"])
    with pytest.raises(AllowedSendersError, match="At most"):
        parse_allowed_senders(
            [f"user{i}@company.com" for i in range(ALLOWED_SENDERS_MAX + 1)]
        )


def test_exact_address_and_subdomain_match() -> None:
    rules = parse_allowed_senders(["alerts@jira.com", "company.com"])
    assert sender_is_allowed("Alerts <alerts@jira.com>", rules) is True
    assert sender_is_allowed("alerts@JIRA.com", rules) is True
    assert sender_is_allowed("bot@alerts.company.com", rules) is True
    assert sender_is_allowed("bot@company.com", rules) is True
    assert sender_is_allowed("mom@gmail.com", rules) is False
    assert sender_is_allowed("user@not-company.com", rules) is False
    assert sender_is_allowed("", rules) is False
    assert parse_from_address("Not an address") is None


def test_search_criteria_date_only_when_allow_list_empty() -> None:
    criteria = build_mailbox_search_criteria(START, END, ())
    assert criteria == '(SINCE "01-Jan-2024" BEFORE "01-Jan-2025")'
    assert "FROM" not in criteria


def test_search_criteria_adds_from_or_keys() -> None:
    rules = parse_allowed_senders(["a@x.com", "y.com"])
    criteria = build_mailbox_search_criteria(START, END, rules)
    assert 'FROM "a@x.com"' in criteria
    assert 'FROM "y.com"' in criteria
    assert "OR " in criteria
    assert "SINCE" in criteria


def _header_peek(from_addr: str) -> tuple:
    payload = f"From: {from_addr}\r\n\r\n".encode("ascii")
    return ("OK", [(b"1 (BODY[HEADER.FIELDS (FROM)] {n}", payload)])


def test_rejected_sender_never_selected_for_body_fetch() -> None:
    allow_list = parse_allowed_senders(["alerts@jira.com"])
    client = MagicMock()

    def fetch(message_set: str, message_parts: str) -> tuple:
        assert "RFC822" not in message_parts
        assert "HEADER" in message_parts
        if message_set == "1":
            return _header_peek("alerts@jira.com")
        if message_set == "2":
            return _header_peek("mom@gmail.com")
        raise AssertionError(message_set)

    client.fetch.side_effect = fetch
    allowed = email_ids_allowed_for_body_fetch(client, ["1", "2"], allow_list)
    assert allowed == ["1"]
    peeked = [call.kwargs["message_set"] for call in client.fetch.call_args_list]
    assert peeked == ["1", "2"]
    assert all(
        "BODY.PEEK[HEADER.FIELDS (FROM)]" in call.kwargs["message_parts"]
        for call in client.fetch.call_args_list
    )


def test_empty_allow_list_skips_header_peek() -> None:
    client = MagicMock()
    allowed = email_ids_allowed_for_body_fetch(client, ["1", "2"], ())
    assert allowed == ["1", "2"]
    client.fetch.assert_not_called()


def test_unparseable_from_is_fail_closed() -> None:
    allow_list = parse_allowed_senders(["alerts@jira.com"])
    client = MagicMock()
    client.fetch.return_value = ("OK", [(b"1 (BODY[HEADER.FIELDS (FROM)] {n}", b"\r\n")])
    assert email_ids_allowed_for_body_fetch(client, ["1"], allow_list) == []
