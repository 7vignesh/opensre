"""Secure local storage helpers for LLM API keys."""

from __future__ import annotations

import os
import platform
import shutil
from typing import Final

import keyring  # type: ignore[import-not-found,import-untyped]
import keyring.errors  # type: ignore[import-not-found,import-untyped]
from pydantic import ValidationError

_KEYRING_SERVICE: Final = "opensre.llm"
_DISABLED_VALUES: Final = frozenset({"1", "true", "yes", "on"})


def _keyring_is_disabled() -> bool:
    return os.getenv("OPENSRE_DISABLE_KEYRING", "").strip().lower() in _DISABLED_VALUES


def resolve_llm_api_key(env_var: str) -> str:
    """Resolve an LLM API key from env first, then the local keychain."""
    env_value = os.getenv(env_var, "").strip()
    if env_value:
        return env_value
    if _keyring_is_disabled():
        return ""
    try:
        return (keyring.get_password(_KEYRING_SERVICE, env_var) or "").strip()
    except keyring.errors.KeyringError:
        return ""


def has_llm_api_key(env_var: str) -> bool:
    """Return True when an API key is available from env or secure local storage."""
    return bool(resolve_llm_api_key(env_var))


def _is_only_missing_llm_api_key_validation(exc: ValidationError) -> bool:
    """True when the only failure is LLMSettings' missing-key model validator."""
    errors = exc.errors()
    if len(errors) != 1:
        return False
    err = errors[0]
    if err.get("type") != "value_error":
        return False
    if err.get("loc") != ():
        return False
    msg = str(err.get("msg", ""))
    return "LLM provider" in msg and "requires" in msg and "API_KEY" in msg and "to be set" in msg


def has_credentials_for_active_llm_provider() -> bool:
    """Return True when :meth:`app.config.LLMSettings.from_env` succeeds.

    Runs full LLM env validation (provider, model names, ``LLM_MAX_TOKENS``, keys via
    :func:`resolve_llm_api_key`, etc.). Callers such as synthetic tests skip only when
    validation fails *solely* because the active provider's API key is absent; any other
    misconfiguration is re-raised so the run fails loudly.
    """
    from app.config import LLMSettings

    try:
        LLMSettings.from_env()
        return True
    except ValidationError as exc:
        if _is_only_missing_llm_api_key_validation(exc):
            return False
        raise


def _keyring_backend_name() -> str:
    backend = keyring.get_keyring()
    return f"{backend.__class__.__module__}.{backend.__class__.__name__}"


def get_keyring_setup_instructions(env_var: str) -> tuple[str, ...]:
    """Return platform-specific guidance for fixing secure credential storage."""
    if _keyring_is_disabled():
        return (
            "Secure local credential storage is disabled by OPENSRE_DISABLE_KEYRING.",
            f"Unset OPENSRE_DISABLE_KEYRING and rerun `opensre onboard` to save {env_var} securely.",
        )

    backend_name = _keyring_backend_name()
    if platform.system() == "Linux":
        lines = [f"Current keyring backend: {backend_name}."]
        if shutil.which("gnome-keyring-daemon") is None:
            lines.append("This Ubuntu or EC2 instance is missing the GNOME Keyring daemon.")
            lines.append(
                "Install it first: sudo apt update && sudo apt install -y gnome-keyring dbus-user-session"
            )
        elif not os.getenv("DBUS_SESSION_BUS_ADDRESS", "").strip():
            lines.append(
                "GNOME Keyring is installed, but this shell is not running inside a D-Bus session."
            )
        else:
            lines.append(
                "This shell has D-Bus available, but the login keyring is still locked or not initialized."
            )

        lines.extend(
            [
                "Start a D-Bus shell: dbus-run-session -- sh",
                "Inside that shell unlock the keyring: echo '<choose-a-keyring-password>' | gnome-keyring-daemon --unlock",
                "Then rerun `opensre onboard` in that same shell.",
                "For deeper diagnostics run `python -m keyring diagnose`.",
            ]
        )
        return tuple(lines)

    return (
        f"Current keyring backend: {backend_name}.",
        "Make sure your system keychain service is installed and unlocked, then rerun `opensre onboard`.",
        "For deeper diagnostics run `python -m keyring diagnose`.",
    )


def save_llm_api_key(env_var: str, value: str) -> None:
    """Persist an LLM API key in the user's system keychain."""
    normalized = value.strip()
    if not normalized:
        delete_llm_api_key(env_var)
        return
    if _keyring_is_disabled():
        raise RuntimeError("Secure local credential storage is disabled on this machine.")
    try:
        keyring.set_password(_KEYRING_SERVICE, env_var, normalized)
    except keyring.errors.KeyringError as exc:
        raise RuntimeError(
            "Secure local credential storage is unavailable on this machine."
        ) from exc


def delete_llm_api_key(env_var: str) -> None:
    """Remove an LLM API key from the user's system keychain if present."""
    if _keyring_is_disabled():
        return
    try:
        keyring.delete_password(_KEYRING_SERVICE, env_var)
    except keyring.errors.KeyringError:
        return
