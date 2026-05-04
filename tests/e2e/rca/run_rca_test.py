#!/usr/bin/env python3
"""Run RCA investigations from markdown alert files in tests/e2e/rca/.

Usage:
    python -m tests.e2e.rca.run_rca_test                    # run all .md files
    python -m tests.e2e.rca.run_rca_test pipeline_error_in_logs  # run one (with or without .md)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from app.pipeline.runners import run_investigation

RCA_DIR = Path(__file__).parent


def _parse_alert_md(path: Path) -> dict[str, Any]:
    """Extract title, severity, pipeline_name, and raw_alert JSON from a markdown alert file."""
    text = path.read_text()

    title_match = re.search(r"^#\s+Alert:\s+(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem

    meta_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    meta: dict[str, Any] = json.loads(meta_match.group(1)) if meta_match else {}
    answer_key_path = meta.get("answer_key_path")

    labels = meta.get("commonLabels", {})
    severity = labels.get("severity", "critical")
    pipeline_name = labels.get("pipeline_name") or labels.get("grafana_folder") or "unknown"

    return {
        "title": title,
        "severity": severity,
        "pipeline_name": pipeline_name,
        "raw_alert": meta,
        "answer_key_path": answer_key_path,
    }


def _validate_against_answer_key(state: dict[str, Any], answer_path: Path) -> tuple[bool, str]:
    answer = yaml.safe_load(answer_path.read_text(encoding="utf-8")) or {}
    expected_category = str(answer.get("root_cause_category") or "").strip()
    required_keywords = [str(keyword).strip() for keyword in answer.get("required_keywords") or []]

    root_cause = str(state.get("root_cause") or "").strip()
    actual_category = str(state.get("root_cause_category") or "").strip()
    evidence_text = " ".join(
        [
            root_cause,
            " ".join(claim.get("claim", "") for claim in state.get("validated_claims", [])),
            " ".join(claim.get("claim", "") for claim in state.get("non_validated_claims", [])),
            " ".join(state.get("causal_chain", [])),
            str(state.get("report") or ""),
            str((state.get("problem_report") or {}).get("report_md") or ""),
        ]
    ).lower()

    if not root_cause:
        return False, "missing root cause"
    if actual_category != expected_category:
        return False, f"expected {expected_category!r}, got {actual_category!r}"

    missing_keywords = [
        keyword for keyword in required_keywords if keyword.lower() not in evidence_text
    ]
    if missing_keywords:
        return False, f"missing keywords: {missing_keywords}"

    return True, ""


def run_file(path: Path) -> bool:
    print(f"\n  RCA TEST  {path.stem}")

    alert = _parse_alert_md(path)

    state = run_investigation(
        alert_name=alert["title"],
        pipeline_name=alert["pipeline_name"],
        severity=alert["severity"],
        raw_alert=alert["raw_alert"],
    )

    passed = bool(state.get("root_cause"))
    failure_reason = ""
    answer_key_path = alert.get("answer_key_path")
    if answer_key_path:
        passed, failure_reason = _validate_against_answer_key(
            state,
            (RCA_DIR.parents[2] / str(answer_key_path)).resolve(),
        )

    category = state.get("root_cause_category") or "—"
    mark = "\033[1;32m●\033[0m" if passed else "\033[1;31m●\033[0m"
    status = "pass" if passed else "fail"
    suffix = f"  {failure_reason}" if failure_reason else ""
    print(f"\n  {mark}  {status}  {path.stem}  {category}{suffix}")
    return passed


def main() -> None:
    if len(sys.argv) > 1:
        name = sys.argv[1]
        if not name.endswith(".md"):
            name += ".md"
        targets = [RCA_DIR / name]
    else:
        targets = sorted(RCA_DIR.glob("*.md"))

    if not targets:
        print("No markdown alert files found in tests/rca/")
        sys.exit(1)

    results = [run_file(p) for p in targets]

    total, passed = len(results), sum(results)
    mark = "\033[1;32m●\033[0m" if passed == total else "\033[1;31m●\033[0m"
    print(f"\n  {mark}  {passed}/{total} passed\n")
    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
