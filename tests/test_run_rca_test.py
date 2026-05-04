from __future__ import annotations

import json
from pathlib import Path

import pytest

import tests.e2e.rca.run_rca_test as rca_runner


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

    passed, reason = rca_runner._validate_against_answer_key(state, answer_path)

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

    passed, reason = rca_runner._validate_against_answer_key(state, answer_path)

    assert passed is False
    assert "missing keywords" in reason


def test_run_file_uses_answer_key_path_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path
    rca_dir = repo_root / "tests" / "e2e" / "rca"
    rca_dir.mkdir(parents=True)
    synthetic_answer_dir = (
        repo_root / "tests" / "synthetic" / "rds_postgres" / "002-connection-exhaustion"
    )
    synthetic_answer_dir.mkdir(parents=True)

    answer_path = synthetic_answer_dir / "answer.yml"
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

    alert_path = rca_dir / "rds-connection-exhaustion.md"
    alert_path.write_text(
        "\n".join(
            [
                "# Alert: [synthetic-rds] Connection Exhaustion On payments-prod",
                "",
                "```json",
                json.dumps(
                    {
                        "title": "[synthetic-rds] Connection Exhaustion On payments-prod",
                        "state": "alerting",
                        "answer_key_path": "tests/synthetic/rds_postgres/002-connection-exhaustion/answer.yml",
                        "commonLabels": {
                            "severity": "critical",
                            "pipeline_name": "rds-postgres-synthetic",
                        },
                    }
                ),
                "```",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        rca_runner,
        "RCA_DIR",
        rca_dir,
    )
    monkeypatch.setattr(
        rca_runner,
        "run_investigation",
        lambda *_args, **_kwargs: {
            "root_cause": "Connection exhaustion consumed max_connections on the database.",
            "root_cause_category": "resource_exhaustion",
            "validated_claims": [{"claim": "The connection pressure remained high."}],
            "non_validated_claims": [],
            "causal_chain": ["Idle sessions exhausted the pool."],
            "report": "",
            "problem_report": {"report_md": ""},
        },
    )

    assert rca_runner.run_file(alert_path) is True
