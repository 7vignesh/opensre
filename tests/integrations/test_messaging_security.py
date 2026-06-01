"""Tests for app.integrations.messaging_security — identity model, pairing, and authorization."""

from __future__ import annotations

import time

import pytest

from app.integrations.messaging_security import (
    _MAX_PAIRING_ATTEMPTS,
    _PAIRING_CODE_TTL_SECONDS,
    AuthorizationResult,
    MessagingIdentityPolicy,
    MessagingPlatform,
    RejectionBehavior,
    authorize_inbound_message,
    complete_pairing,
    complete_pairing_with_persistence,
    generate_pairing_code,
    hash_pairing_code,
    verify_pairing_code,
)

# ---------------------------------------------------------------------------
# MessagingIdentityPolicy model tests
# ---------------------------------------------------------------------------


class TestMessagingIdentityPolicy:
    def test_default_policy_has_empty_allowlists(self) -> None:
        policy = MessagingIdentityPolicy()
        assert policy.allowed_user_ids == []
        assert policy.allowed_chat_ids == []
        assert policy.require_dm_pairing is True
        assert policy.pairing_secret_hash is None
        assert policy.pairing_created_at is None
        assert policy.pairing_attempts == 0
        assert policy.rejection_behavior == RejectionBehavior.REPLY
        assert policy.inbound_enabled is False

    def test_policy_with_allowed_users(self) -> None:
        policy = MessagingIdentityPolicy(
            allowed_user_ids=["123", "456"],
            inbound_enabled=True,
        )
        assert policy.allowed_user_ids == ["123", "456"]
        assert policy.inbound_enabled is True

    def test_policy_serialization_roundtrip(self) -> None:
        policy = MessagingIdentityPolicy(
            allowed_user_ids=["user1"],
            allowed_chat_ids=["chat1"],
            require_dm_pairing=False,
            rejection_behavior=RejectionBehavior.DROP,
            inbound_enabled=True,
        )
        data = policy.model_dump(mode="json")
        restored = MessagingIdentityPolicy.model_validate(data)
        assert restored.allowed_user_ids == ["user1"]
        assert restored.allowed_chat_ids == ["chat1"]
        assert restored.require_dm_pairing is False
        assert restored.rejection_behavior == RejectionBehavior.DROP
        assert restored.inbound_enabled is True

    def test_policy_back_compat_empty_dict_loads(self) -> None:
        """Existing configs with no identity_policy should load with defaults."""
        policy = MessagingIdentityPolicy.model_validate({})
        assert policy.allowed_user_ids == []
        assert policy.inbound_enabled is False


# ---------------------------------------------------------------------------
# Pairing code tests
# ---------------------------------------------------------------------------


class TestPairingCode:
    def test_generate_pairing_code_length(self) -> None:
        code = generate_pairing_code()
        assert len(code) == 6

    def test_generate_pairing_code_is_alphanumeric_uppercase(self) -> None:
        code = generate_pairing_code()
        assert code.isalnum()
        assert code == code.upper()

    def test_generate_pairing_code_is_random(self) -> None:
        codes = {generate_pairing_code() for _ in range(100)}
        # With 36^6 possible codes, 100 should all be unique
        assert len(codes) == 100

    def test_hash_pairing_code_deterministic(self) -> None:
        code = "ABC123"
        h1 = hash_pairing_code(code)
        h2 = hash_pairing_code(code)
        assert h1 == h2

    def test_hash_pairing_code_case_insensitive(self) -> None:
        assert hash_pairing_code("ABC123") == hash_pairing_code("abc123")

    def test_hash_pairing_code_is_hex_string(self) -> None:
        h = hash_pairing_code("TEST01")
        assert len(h) == 64  # SHA-256 hex
        int(h, 16)  # Should not raise

    def test_verify_pairing_code_correct(self) -> None:
        code = "XYZ789"
        stored = hash_pairing_code(code)
        assert verify_pairing_code(code, stored) is True

    def test_verify_pairing_code_wrong(self) -> None:
        stored = hash_pairing_code("CORRECT")
        assert verify_pairing_code("WRONG1", stored) is False

    def test_verify_pairing_code_case_insensitive(self) -> None:
        code = "ABC123"
        stored = hash_pairing_code(code)
        assert verify_pairing_code("abc123", stored) is True


# ---------------------------------------------------------------------------
# Authorization tests
# ---------------------------------------------------------------------------


class TestAuthorizeInboundMessage:
    def test_inbound_disabled_rejects(self) -> None:
        policy = MessagingIdentityPolicy(inbound_enabled=False)
        result = authorize_inbound_message(policy=policy, user_id="123")
        assert not result.allowed
        assert "not enabled" in result.reason

    def test_pairing_attempt_allowed_when_pending(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code("ABC123"),
        )
        result = authorize_inbound_message(
            policy=policy, user_id="unknown", message_text="/pair ABC123"
        )
        assert result.allowed
        assert result.is_pairing_attempt

    def test_pairing_attempt_rejected_when_no_pending(self) -> None:
        """When no pairing is pending, /pair messages should be rejected."""
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=None,
        )
        result = authorize_inbound_message(
            policy=policy, user_id="unknown", message_text="/pair ABC123"
        )
        assert not result.allowed
        assert "no pairing" in result.reason.lower()

    def test_pairing_attempt_case_insensitive(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code("ABC123"),
        )
        result = authorize_inbound_message(
            policy=policy, user_id="unknown", message_text="/Pair abc123"
        )
        assert result.allowed
        assert result.is_pairing_attempt

    def test_allowed_user_passes(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_user_ids=["user1", "user2"],
        )
        result = authorize_inbound_message(policy=policy, user_id="user1")
        assert result.allowed

    def test_disallowed_user_rejected(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_user_ids=["user1"],
        )
        result = authorize_inbound_message(policy=policy, user_id="intruder")
        assert not result.allowed
        assert "not in the allowed" in result.reason

    def test_empty_allowlist_with_pairing_required_rejects(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            require_dm_pairing=True,
            allowed_user_ids=[],
        )
        result = authorize_inbound_message(policy=policy, user_id="anyone")
        assert not result.allowed
        assert "No users have been paired" in result.reason

    def test_empty_allowlist_without_pairing_allows(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            require_dm_pairing=False,
            allowed_user_ids=[],
        )
        result = authorize_inbound_message(policy=policy, user_id="anyone")
        assert result.allowed

    def test_chat_id_restriction(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_user_ids=["user1"],
            allowed_chat_ids=["chat1"],
        )
        # Allowed chat
        result = authorize_inbound_message(policy=policy, user_id="user1", chat_id="chat1")
        assert result.allowed

        # Disallowed chat
        result = authorize_inbound_message(policy=policy, user_id="user1", chat_id="other_chat")
        assert not result.allowed
        assert "not in the allowed chat" in result.reason

    def test_chat_id_none_blocked_when_allowlist_configured(self) -> None:
        """When allowed_chat_ids is set, None chat_id should be blocked."""
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_user_ids=["user1"],
            allowed_chat_ids=["chat1"],
        )
        result = authorize_inbound_message(policy=policy, user_id="user1", chat_id=None)
        assert not result.allowed
        assert "not in the allowed chat" in result.reason

    def test_pairing_blocked_from_restricted_chat(self) -> None:
        """Pairing attempts from outside allowed_chat_ids are blocked."""
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_chat_ids=["chat1"],
            pairing_secret_hash=hash_pairing_code("CODE01"),
        )
        result = authorize_inbound_message(
            policy=policy, user_id="anyone", chat_id="other_chat", message_text="/pair CODE01"
        )
        assert not result.allowed
        assert "not in the allowed chat" in result.reason

    def test_authorization_result_bool(self) -> None:
        allowed = AuthorizationResult(allowed=True, reason="ok")
        denied = AuthorizationResult(allowed=False, reason="no")
        assert bool(allowed) is True
        assert bool(denied) is False


# ---------------------------------------------------------------------------
# Complete pairing tests
# ---------------------------------------------------------------------------


class TestCompletePairing:
    def test_successful_pairing(self) -> None:
        code = "TEST01"
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code(code),
            pairing_created_at=time.time(),
        )
        success, message = complete_pairing(policy=policy, user_id="new_user", code=code)
        assert success is True
        assert "successful" in message.lower()
        assert "new_user" in policy.allowed_user_ids
        assert policy.pairing_secret_hash is None
        assert policy.pairing_attempts == 0

    def test_pairing_wrong_code_increments_attempts(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code("CORRECT"),
            pairing_created_at=time.time(),
        )
        success, message = complete_pairing(policy=policy, user_id="user1", code="WRONG1")
        assert success is False
        assert "invalid" in message.lower()
        assert "user1" not in policy.allowed_user_ids
        # Hash should NOT be cleared on single failure
        assert policy.pairing_secret_hash is not None
        assert policy.pairing_attempts == 1

    def test_pairing_no_pending(self) -> None:
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=None,
        )
        success, message = complete_pairing(policy=policy, user_id="user1", code="ANY123")
        assert success is False
        assert "no pairing" in message.lower()

    def test_pairing_does_not_duplicate_user(self) -> None:
        code = "DUP001"
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            allowed_user_ids=["existing_user"],
            pairing_secret_hash=hash_pairing_code(code),
            pairing_created_at=time.time(),
        )
        success, _ = complete_pairing(policy=policy, user_id="existing_user", code=code)
        assert success is True
        assert policy.allowed_user_ids.count("existing_user") == 1

    def test_brute_force_invalidates_code(self) -> None:
        """After MAX_PAIRING_ATTEMPTS failures, the code is invalidated."""
        code = "SECRET"
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code(code),
            pairing_created_at=time.time(),
        )
        # Exhaust all attempts
        for i in range(_MAX_PAIRING_ATTEMPTS):
            success, _ = complete_pairing(policy=policy, user_id="attacker", code=f"WRONG{i}")
            assert success is False

        # Code should now be invalidated
        assert policy.pairing_secret_hash is None
        assert policy.pairing_attempts == 0

    def test_expired_code_rejected(self) -> None:
        """A pairing code that has exceeded its TTL is rejected."""
        code = "EXPIRE"
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code(code),
            pairing_created_at=time.time() - _PAIRING_CODE_TTL_SECONDS - 1,
        )
        success, message = complete_pairing(policy=policy, user_id="user1", code=code)
        assert success is False
        assert "expired" in message.lower()
        assert policy.pairing_secret_hash is None

    def test_missing_pairing_created_at_treated_as_expired(self) -> None:
        """A hash with no timestamp (legacy or corrupted) is treated as expired."""
        code = "LEGACY"
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code(code),
            pairing_created_at=None,
        )
        success, message = complete_pairing(policy=policy, user_id="user1", code=code)
        assert success is False
        assert "expired" in message.lower()
        assert policy.pairing_secret_hash is None


# ---------------------------------------------------------------------------
# Persistence-aware pairing wrapper tests
# ---------------------------------------------------------------------------


class _FakePolicyStore:
    """In-memory stand-in for a durable identity-policy store.

    Simulates a real persistence boundary: callers mutate a loaded policy,
    then must write it back. ``load`` always returns a fresh object parsed
    from the last saved snapshot, so any mutation that was not persisted is
    lost across a load — exactly the failure mode the wrapper must prevent.
    """

    def __init__(self, policy: MessagingIdentityPolicy) -> None:
        self._snapshot = policy.model_dump(mode="json")
        self.save_calls = 0

    def load(self) -> MessagingIdentityPolicy:
        return MessagingIdentityPolicy.model_validate(self._snapshot)

    def save(self, policy: MessagingIdentityPolicy) -> None:
        self.save_calls += 1
        self._snapshot = policy.model_dump(mode="json")


class TestCompletePairingWithPersistence:
    def test_persists_on_success(self) -> None:
        code = "OK0001"
        store = _FakePolicyStore(
            MessagingIdentityPolicy(
                inbound_enabled=True,
                pairing_secret_hash=hash_pairing_code(code),
                pairing_created_at=time.time(),
            )
        )
        policy = store.load()
        success, _ = complete_pairing_with_persistence(
            policy=policy, user_id="new_user", code=code, persist=store.save
        )
        assert success is True
        assert store.save_calls == 1
        # Reload from the store: the successful pairing is durable.
        reloaded = store.load()
        assert "new_user" in reloaded.allowed_user_ids
        assert reloaded.pairing_secret_hash is None

    def test_persists_on_failed_attempt(self) -> None:
        """A wrong code must persist the incremented counter, not just on success."""
        store = _FakePolicyStore(
            MessagingIdentityPolicy(
                inbound_enabled=True,
                pairing_secret_hash=hash_pairing_code("CORRECT"),
                pairing_created_at=time.time(),
            )
        )
        policy = store.load()
        success, _ = complete_pairing_with_persistence(
            policy=policy, user_id="attacker", code="WRONG1", persist=store.save
        )
        assert success is False
        assert store.save_calls == 1
        # The increment survived the round-trip through the store.
        assert store.load().pairing_attempts == 1

    def test_brute_force_invalidated_across_reload_boundaries(self) -> None:
        """Drive failed attempts each on a freshly loaded policy.

        This is the core regression for issue #2677: each attempt loads a
        fresh policy from the store (as a stateless request handler would),
        mutates it, and persists via the wrapper. The counter must accumulate
        and the code must be invalidated after _MAX_PAIRING_ATTEMPTS — proving
        protection does not depend on the caller reusing one in-memory object.
        """
        store = _FakePolicyStore(
            MessagingIdentityPolicy(
                inbound_enabled=True,
                pairing_secret_hash=hash_pairing_code("SECRET"),
                pairing_created_at=time.time(),
            )
        )
        for i in range(_MAX_PAIRING_ATTEMPTS):
            policy = store.load()  # stateless reload before each attempt
            success, _ = complete_pairing_with_persistence(
                policy=policy, user_id="attacker", code=f"WRONG{i}", persist=store.save
            )
            assert success is False

        final = store.load()
        assert final.pairing_secret_hash is None
        # A subsequent guess with the (now cleared) code cannot succeed.
        success, message = complete_pairing_with_persistence(
            policy=store.load(), user_id="attacker", code="SECRET", persist=store.save
        )
        assert success is False
        assert "no pairing" in message.lower()

    def test_naive_persist_only_on_success_would_defeat_protection(self) -> None:
        """Contrast test: persisting only on success never invalidates the code.

        Documents the exact footgun the wrapper removes. Using bare
        complete_pairing and skipping persistence on failure, a fresh reload
        each attempt resets pairing_attempts, so the code is never invalidated.
        """
        store = _FakePolicyStore(
            MessagingIdentityPolicy(
                inbound_enabled=True,
                pairing_secret_hash=hash_pairing_code("SECRET"),
                pairing_created_at=time.time(),
            )
        )
        for i in range(_MAX_PAIRING_ATTEMPTS * 3):
            policy = store.load()
            success, _ = complete_pairing(policy=policy, user_id="attacker", code=f"WRONG{i}")
            assert success is False
            # Naive caller: only persists on success → nothing saved here.

        # Counter never accumulated; code is still live and brute-forceable.
        assert store.load().pairing_attempts == 0
        assert store.load().pairing_secret_hash is not None

    def test_persist_called_when_complete_pairing_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """persist runs even if the inner call raises (finally semantics)."""
        import app.integrations.messaging_security as ms

        calls: list[str] = []

        def boom(_policy: MessagingIdentityPolicy) -> None:
            calls.append("persisted")

        def raising(**_kwargs: object) -> tuple[bool, str]:
            raise RuntimeError("inner failure")

        monkeypatch.setattr(ms, "complete_pairing", raising)

        policy = MessagingIdentityPolicy(inbound_enabled=True)
        with pytest.raises(RuntimeError, match="inner failure"):
            complete_pairing_with_persistence(policy=policy, user_id="u", code="X", persist=boom)

        assert calls == ["persisted"]

    def test_persist_failure_is_raised_not_swallowed(self) -> None:
        """If the persist callback fails, the error must propagate."""
        code = "OK0002"
        policy = MessagingIdentityPolicy(
            inbound_enabled=True,
            pairing_secret_hash=hash_pairing_code(code),
            pairing_created_at=time.time(),
        )

        def failing_persist(_policy: MessagingIdentityPolicy) -> None:
            raise OSError("disk full")

        with pytest.raises(OSError, match="disk full"):
            complete_pairing_with_persistence(
                policy=policy, user_id="u", code=code, persist=failing_persist
            )

    def test_double_failure_persist_replaces_original_with_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When both complete_pairing and persist raise, persist wins but original is __context__.

        Python's finally semantics mean the persist exception replaces the
        in-flight complete_pairing exception. The original is preserved as
        __context__ so it remains visible in tracebacks. This test documents
        that behavior explicitly (Greptile P2 review feedback).
        """
        import app.integrations.messaging_security as ms

        def raising_pairing(**_kwargs: object) -> tuple[bool, str]:
            raise ValueError("pairing logic exploded")

        def raising_persist(_policy: MessagingIdentityPolicy) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(ms, "complete_pairing", raising_pairing)

        policy = MessagingIdentityPolicy(inbound_enabled=True)

        # The persist exception is what surfaces to the caller.
        with pytest.raises(OSError, match="disk full") as exc_info:
            complete_pairing_with_persistence(
                policy=policy, user_id="u", code="X", persist=raising_persist
            )

        # The original pairing exception is preserved as __context__.
        assert exc_info.value.__context__ is not None
        assert isinstance(exc_info.value.__context__, ValueError)
        assert "pairing logic exploded" in str(exc_info.value.__context__)


# ---------------------------------------------------------------------------
# Platform enum tests
# ---------------------------------------------------------------------------


class TestMessagingPlatform:
    def test_platform_values(self) -> None:
        assert MessagingPlatform.TELEGRAM.value == "telegram"
        assert MessagingPlatform.SLACK.value == "slack"
        assert MessagingPlatform.DISCORD.value == "discord"
