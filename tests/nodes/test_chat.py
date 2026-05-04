"""Tests for chat branch LangChain LLM selection (LLM_PROVIDER)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.constants.prompts import ROUTER_PROMPT
from app.nodes import chat as chat_mod


def _clear_chat_llm_singletons() -> None:
    """Reset module-level chat model cache (isolated test runs)."""
    chat_mod._chat_llm_cache.clear()
    chat_mod._chat_llm_with_tools_cache.clear()


class _OpenAIModule:
    def __init__(self, chat_openai: MagicMock) -> None:
        self.ChatOpenAI = chat_openai


class _AnthropicModule:
    def __init__(self, chat_anthropic: MagicMock) -> None:
        self.ChatAnthropic = chat_anthropic


def _synthetic_rds_message() -> str:
    alert_path = (
        Path(__file__).parent.parent
        / "synthetic"
        / "rds_postgres"
        / "002-connection-exhaustion"
        / "alert.json"
    )
    alert = json.loads(alert_path.read_text(encoding="utf-8"))

    return (
        f"[synthetic-rds] {alert['title']} | "
        f"state={alert['state']} | "
        f"alertname={alert['commonLabels']['alertname']} | "
        f"severity={alert['commonLabels']['severity']} | "
        f"summary={alert['commonAnnotations']['summary']}"
    )


@pytest.fixture
def openai_chat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


@pytest.mark.usefixtures("openai_chat_env")
def test_get_chat_llm_openai_with_tools_uses_chat_openai() -> None:
    _clear_chat_llm_singletons()
    with patch.object(chat_mod, "import_module") as mock_import_module:
        mock_base = MagicMock()
        mock_bound = MagicMock()
        mock_base.bind_tools.return_value = mock_bound
        mock_openai = MagicMock(return_value=mock_base)
        mock_import_module.return_value = _OpenAIModule(mock_openai)
        out = chat_mod._get_chat_llm(with_tools=True)
        mock_openai.assert_called_once()
        assert out is mock_bound


@pytest.mark.usefixtures("openai_chat_env")
def test_get_chat_llm_openai_without_tools_uses_chat_openai() -> None:
    _clear_chat_llm_singletons()
    with patch.object(chat_mod, "import_module") as mock_import_module:
        mock_llm = MagicMock()
        mock_openai = MagicMock(return_value=mock_llm)
        mock_import_module.return_value = _OpenAIModule(mock_openai)
        out = chat_mod._get_chat_llm(with_tools=False)
        mock_openai.assert_called_once()
        assert out is mock_llm


def test_get_chat_llm_anthropic_uses_chat_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    _clear_chat_llm_singletons()
    with patch.object(chat_mod, "import_module") as mock_import_module:
        mock_llm = MagicMock()
        mock_anthropic = MagicMock(return_value=mock_llm)
        mock_import_module.return_value = _AnthropicModule(mock_anthropic)
        out = chat_mod._get_chat_llm(with_tools=False)
        mock_anthropic.assert_called_once()
        assert out is mock_llm


def test_general_node_returns_user_facing_message_for_codex_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "codex")
    chat_mod._chat_llm_cache.clear()
    state = {"messages": [{"role": "user", "content": "hello"}]}

    out = chat_mod.general_node(state, {"configurable": {}})

    assert out["messages"]
    assert (
        "Interactive chat requires LLM_PROVIDER=anthropic or openai." in out["messages"][0].content
    )


@pytest.mark.parametrize(
    ("message_content", "expected_route"),
    [
        (_synthetic_rds_message(), "tracer_data"),
        ("What is CrashLoopBackOff and how should SREs reason about it?", "general"),
    ],
)
def test_router_node_routes_representative_inputs_to_expected_labels(
    message_content: str,
    expected_route: str,
) -> None:
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=expected_route)
    state = {"messages": [{"role": "user", "content": message_content}]}

    with patch.object(chat_mod, "get_llm_for_tools", return_value=llm):
        out = chat_mod.router_node(state)

    assert out["route"] == expected_route
    llm.invoke.assert_called_once()


def test_router_node_defaults_unknown_label_to_general() -> None:
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="unknown")
    state = {"messages": [{"role": "user", "content": "hello"}]}

    with patch.object(chat_mod, "get_llm_for_tools", return_value=llm):
        out = chat_mod.router_node(state)

    assert out["route"] == "general"


def test_router_prompt_calls_out_synthetic_alert_payload_signals() -> None:
    # Prefer keyword checks to avoid tight coupling to exact phrasing/punctuation
    lower = ROUTER_PROMPT.lower()
    assert "synthetic" in lower
    assert "alertname" in lower
    assert "state=alerting" in lower or "state = alerting" in lower
    assert "cluster_name" in lower
    assert "db_instance_identifier" in lower


def test_router_prompt_distinguishes_conceptual_questions() -> None:
    # Check for keywords/themes rather than exact sentence fragments
    lower = ROUTER_PROMPT.lower()
    assert "crashloopbackoff" in lower or "crashloop backoff" in lower
    assert "best practice" in lower or "runbook" in lower or "process advice" in lower
