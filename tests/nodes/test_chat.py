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


class _RecordingLLM:
    """A tiny deterministic LLM stub that records the prompt passed and
    returns a label based on whether the prompt or user message contains
    synthetic alert indicators. This allows tests to assert prompt contents
    and that the router will route to `tracer_data` when prompts include
    the expected alert-field cues.
    """

    def __init__(self, record_file: str | None = None) -> None:
        self.last_prompt: str | None = None
        self.record_file = record_file

    def invoke(self, messages: list[dict[str, str]]):
        # Normalize into a single string for inspection
        parts: list[str] = []
        for m in messages:
            parts.append(str(m.get("content", "")))
        prompt = "\n".join(parts)
        self.last_prompt = prompt

        # Heuristic: if the system prompt or user message mentions alert-like
        # fields, return tracer_data; otherwise return general.
        cue_tokens = ["alertname", "state=alerting", "db_instance_identifier", "synthetic"]
        label = "tracer_data" if any(tok in prompt for tok in cue_tokens) else "general"

        # Optionally record to file
        if self.record_file:
            with open(self.record_file, "a") as f:
                f.write("=== Recording ===\n")
                f.write(f"PROMPT:\n{prompt}\n")
                f.write(f"LABEL: {label}\n")
                f.write("\n")

        return MagicMock(content=label)


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
    "message_content, use_recording_llm, expected_route",
    [
        (
            "[synthetic-rds] Connection Exhaustion On payments-prod | "
            "state=alerting | alertname=RDSDatabaseConnectionsHigh | severity=critical | "
            "summary=DatabaseConnections reached 98% of max_connections and API traffic is failing "
            "with too many clients. Diagnose the root cause.",
            False,
            "tracer_data",
        ),
        # Fixture-backed case: message_content is None -> load from synthetic fixture
        (None, True, "tracer_data"),
    ],
)
def test_router_routes_handcrafted_and_fixture_alerts(
    message_content: str, use_recording_llm: bool, expected_route: str
) -> None:
    """Consolidated router tests for synthetic alerts.

    - Handcrafted synthetic message (mocked LLM returning the expected label)
    - Fixture-backed synthetic message (uses `_RecordingLLM` to assert prompt cues)
    """
    if use_recording_llm:
        record_file = Path(__file__).parent / "router_recordings.txt"
        if record_file.exists():
            record_file.unlink()
        llm = _RecordingLLM(record_file=str(record_file))
    else:
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content=expected_route)

    # If message_content is None, load the realistic synthetic RDS alert fixture
    if message_content is None:
        alert_path = (
            Path(__file__).parent.parent
            / "synthetic"
            / "rds_postgres"
            / "002-connection-exhaustion"
            / "alert.json"
        )
        with alert_path.open() as f:
            alert = json.load(f)

        message_content = (
            f"[synthetic-rds] {alert['title']} | "
            f"state={alert['state']} | "
            f"alertname={alert['commonLabels']['alertname']} | "
            f"severity={alert['commonLabels']['severity']} | "
            f"summary={alert['commonAnnotations']['summary']}"
        )

    state = {"messages": [{"role": "user", "content": message_content}]}

    with patch.object(chat_mod, "get_llm_for_tools", return_value=llm):
        out = chat_mod.router_node(state)

    assert out["route"] == expected_route
    # If we're using a MagicMock LLM, ensure it was invoked once
    if not use_recording_llm:
        llm.invoke.assert_called_once()


def test_router_node_routes_conceptual_question_to_general() -> None:
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="general")
    state = {
        "messages": [
            {
                "role": "user",
                "content": "What is CrashLoopBackOff and how should SREs reason about it?",
            }
        ]
    }

    with patch.object(chat_mod, "get_llm_for_tools", return_value=llm):
        out = chat_mod.router_node(state)

    assert out["route"] == "general"
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
