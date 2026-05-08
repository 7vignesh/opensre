"""Messaging security: per-user identity, allowed-users list, and DM pairing.

This module implements the identity model for inbound messaging platforms
(Telegram, Slack, Discord). It provides:

1. MessagingIdentityPolicy — per-platform allowlist and pairing config.
2. DM pairing helpers — one-time code generation, hashing, and verification.
3. Inbound message authorization — check whether a sender is allowed.

Prerequisite for issue #1482 (conversational loop).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import string
from enum import StrEnum

from pydantic import Field

from app.strict_config import StrictConfigModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PAIRING_CODE_LENGTH = 6
_PAIRING_CODE_ALPHABET = string.ascii_uppercase + string.digits


class RejectionBehavior(StrEnum):
    """How to handle messages from non-paired users."""

    REPLY = "reply"
    DROP = "drop"


class MessagingPlatform(StrEnum):
    """Supported messaging platforms."""

    TELEGRAM = "telegram"
    SLACK = "slack"
    DISCORD = "discord"


# ---------------------------------------------------------------------------
# Identity Policy Model
# ---------------------------------------------------------------------------


class MessagingIdentityPolicy(StrictConfigModel):
    """Per-platform identity policy for inbound messaging security.

    Controls which users are allowed to interact with the bot and how
    unauthenticated users are handled.
    """

    allowed_user_ids: list[str] = Field(
        default_factory=list,
        description="Platform-native user IDs allowed to interact (Telegram from.id, Slack user_id, Discord member.user.id)",
    )
    allowed_chat_ids: list[str] = Field(
        default_factory=list,
        description="Optional: restrict interactions to specific channels/chats",
    )
    require_dm_pairing: bool = Field(
        default=True,
        description="Whether users must complete DM pairing before interacting",
    )
    pairing_secret_hash: str | None = Field(
        default=None,
        description="SHA-256 HMAC hash of the one-time pairing code (None = no pending pairing)",
    )
    rejection_behavior: RejectionBehavior = Field(
        default=RejectionBehavior.REPLY,
        description="How to handle messages from non-paired users: 'reply' or 'drop'",
    )
    inbound_enabled: bool = Field(
        default=False,
        description="Whether inbound messaging is enabled for this platform",
    )


# ---------------------------------------------------------------------------
# Pairing Code Helpers
# ---------------------------------------------------------------------------

# HMAC key used for hashing pairing codes. In production this should be
# derived from a per-installation secret; for now we use a fixed namespace
# so that the hash is deterministic given the same code.
_PAIRING_HMAC_KEY = b"opensre-messaging-pairing-v1"


def generate_pairing_code() -> str:
    """Generate a cryptographically random one-time pairing code.

    Returns a 6-character uppercase alphanumeric string.
    """
    return "".join(secrets.choice(_PAIRING_CODE_ALPHABET) for _ in range(_PAIRING_CODE_LENGTH))


def hash_pairing_code(code: str) -> str:
    """Compute a deterministic HMAC-SHA256 hash of a pairing code.

    The hash is stored in the config; the plaintext code is shown to the
    operator once and never persisted.
    """
    return hmac.HMAC(_PAIRING_HMAC_KEY, code.upper().encode(), hashlib.sha256).hexdigest()


def verify_pairing_code(code: str, stored_hash: str) -> bool:
    """Verify a pairing code against its stored hash (constant-time comparison)."""
    computed = hash_pairing_code(code)
    return hmac.compare_digest(computed, stored_hash)


# ---------------------------------------------------------------------------
# Authorization Check
# ---------------------------------------------------------------------------


class AuthorizationResult:
    """Result of an inbound message authorization check."""

    def __init__(
        self,
        *,
        allowed: bool,
        reason: str,
        is_pairing_attempt: bool = False,
    ) -> None:
        self.allowed = allowed
        self.reason = reason
        self.is_pairing_attempt = is_pairing_attempt

    def __bool__(self) -> bool:
        return self.allowed

    def __repr__(self) -> str:
        return f"AuthorizationResult(allowed={self.allowed}, reason={self.reason!r})"


def authorize_inbound_message(
    *,
    policy: MessagingIdentityPolicy,
    user_id: str,
    chat_id: str | None = None,
    message_text: str | None = None,
) -> AuthorizationResult:
    """Check whether an inbound message is authorized under the given policy.

    Returns an AuthorizationResult indicating whether the message should be
    processed, and if not, why.
    """
    if not policy.inbound_enabled:
        return AuthorizationResult(
            allowed=False,
            reason="Inbound messaging is not enabled for this platform",
        )

    # Check if this is a pairing attempt
    if message_text and message_text.strip().lower().startswith("/pair "):
        return AuthorizationResult(
            allowed=True,
            reason="Pairing attempt",
            is_pairing_attempt=True,
        )

    # Check allowed chat IDs (if configured)
    if policy.allowed_chat_ids and chat_id and chat_id not in policy.allowed_chat_ids:
        return AuthorizationResult(
            allowed=False,
            reason=f"Chat {chat_id} is not in the allowed chat list",
        )

    # Check allowed user IDs
    if not policy.allowed_user_ids:
        if policy.require_dm_pairing:
            return AuthorizationResult(
                allowed=False,
                reason="No users have been paired yet. Use /pair <code> to pair.",
            )
        # If pairing is not required and no allowlist, allow all
        return AuthorizationResult(allowed=True, reason="No allowlist configured, open access")

    if user_id not in policy.allowed_user_ids:
        return AuthorizationResult(
            allowed=False,
            reason=f"User {user_id} is not in the allowed users list",
        )

    return AuthorizationResult(allowed=True, reason="User is authorized")


def complete_pairing(
    *,
    policy: MessagingIdentityPolicy,
    user_id: str,
    code: str,
) -> tuple[bool, str]:
    """Attempt to complete DM pairing for a user.

    On success, adds the user to allowed_user_ids and clears the pairing
    secret. Returns (success, message).

    Note: The caller is responsible for persisting the updated policy.
    """
    if not policy.pairing_secret_hash:
        return False, "No pairing is pending. Ask the operator to run `opensre messaging pair`."

    if not verify_pairing_code(code, policy.pairing_secret_hash):
        return False, "Invalid pairing code. Please check and try again."

    # Pairing successful
    if user_id not in policy.allowed_user_ids:
        policy.allowed_user_ids.append(user_id)
    policy.pairing_secret_hash = None

    logger.info("DM pairing completed for user %s", user_id)
    return True, "Pairing successful! You can now interact with the bot."


# ---------------------------------------------------------------------------
# Audit Logging
# ---------------------------------------------------------------------------


def audit_log_inbound_message(
    *,
    platform: str,
    user_id: str,
    chat_id: str | None,
    message_hash: str | None = None,
    authorized: bool,
    reason: str,
) -> None:
    """Emit a structured audit log entry for an inbound message.

    Message body is hashed (not stored in plaintext) to enable misuse
    investigation without leaking content.
    """
    logger.info(
        "[messaging-audit] platform=%s user_id=%s chat_id=%s authorized=%s reason=%s msg_hash=%s",
        platform,
        user_id,
        chat_id or "N/A",
        authorized,
        reason,
        message_hash or "N/A",
    )
