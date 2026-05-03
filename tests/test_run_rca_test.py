from __future__ import annotations

from pathlib import Path

from tests.e2e.rca.run_rca_test import _validate_against_answer_key


def test_validate_against_answer_key_passes_for_expected_diagnosis(tmp_path: Path) -> None:
    answer_path = tmp_path / "answer.yml"
    answer_path.write_text(
        "\n".join(
            [
                "root_cause_category: resource_exhaustion",
                "required_keywords:",
                "  - connection",
                "  - max_connections",
            ]
        ),
        encoding="utf-8",
    )

    state = {
        "root_cause": "Connection exhaustion consumed max_connections on the database.",
        "root_cause_category": "resource_exhaustion",
        "validated_claims": [{"claim": "The connection pressure remained high."}],
        "non_validated_claims": [],
        "causal_chain": ["Idle sessions exhausted the pool."],
        "report": "",
        "problem_report": {"report_md": ""},
    }

    passed, reason = _validate_against_answer_key(state, answer_path)

    assert passed is True
    assert reason == ""


def test_validate_against_answer_key_reports_missing_keywords(tmp_path: Path) -> None:
    answer_path = tmp_path / "answer.yml"
    answer_path.write_text(
        "\n".join(
            [
                "root_cause_category: resource_exhaustion",
                "required_keywords:",
                "  - connection",
                "  - max_connections",
            ]
        ),
        encoding="utf-8",
    )

    state = {
        "root_cause": "Database issue.",
        "root_cause_category": "resource_exhaustion",
        "validated_claims": [],
        "non_validated_claims": [],
        "causal_chain": [],
        "report": "",
        "problem_report": {"report_md": ""},
    }

    passed, reason = _validate_against_answer_key(state, answer_path)

    assert passed is False
    assert "missing keywords" in reason
