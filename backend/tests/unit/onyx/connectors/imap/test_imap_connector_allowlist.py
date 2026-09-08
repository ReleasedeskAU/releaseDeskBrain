"""ImapConnector rejects an invalid allow-list at construct time."""

import pytest

from onyx.connectors.exceptions import ConnectorValidationError
from onyx.connectors.imap.connector import ImapConnector


def test_invalid_allow_list_fails_connector_init() -> None:
    with pytest.raises(ConnectorValidationError, match="quotes"):
        ImapConnector(host="imap.example.com", allowed_senders=['bad"'])
