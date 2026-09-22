from shared_configs.contextvars import UsageCredentialIdentity
from shared_configs.enums import UsageCredentialType

from onyx.server.documents.unmasked_credential import is_service_credential


def test_service_pat_and_api_key_are_allowed() -> None:
    assert is_service_credential(
        UsageCredentialIdentity(UsageCredentialType.PAT)
    )
    assert is_service_credential(
        UsageCredentialIdentity(UsageCredentialType.API_KEY, "1", "desk", "on_ab********yz")
    )


def test_session_jwt_and_craft_pat_are_refused() -> None:
    assert not is_service_credential(None)
    assert not is_service_credential(
        UsageCredentialIdentity(UsageCredentialType.SESSION)
    )
    assert not is_service_credential(
        UsageCredentialIdentity(UsageCredentialType.JWT)
    )
    assert not is_service_credential(
        UsageCredentialIdentity(UsageCredentialType.CRAFT_PAT)
    )
