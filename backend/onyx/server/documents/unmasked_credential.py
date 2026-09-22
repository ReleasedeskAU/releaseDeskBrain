"""Service-PAT gate for reading a connector credential without masking.

The normal credential APIs apply ``mask_credential_prefix``. Edit-to-add-scope
needs the real secret inside the index engine, then only on the Release Desk
server. Session and Craft tokens are refused so a browser cookie cannot call
this route.
"""

from shared_configs.contextvars import UsageCredentialIdentity
from shared_configs.enums import UsageCredentialType

# Release Desk authenticates with STAFFLESS_AI_PAT or ONYX_API_KEY.
# Both are bearer tokens held only on the server. Craft PATs and sessions are not.
SERVICE_CREDENTIAL_TYPES = frozenset(
    {UsageCredentialType.PAT, UsageCredentialType.API_KEY}
)


def is_service_credential(identity: UsageCredentialIdentity | None) -> bool:
    """True when this request authenticated with the service PAT or API key.

    @param identity - ``request.state.usage_credential``, or None when absent.
    @returns Whether an unmasked credential read is allowed.
    """
    if identity is None:
        return False
    return identity.credential_type in SERVICE_CREDENTIAL_TYPES
