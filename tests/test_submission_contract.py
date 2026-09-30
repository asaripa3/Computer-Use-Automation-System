"""The submission-facing files are part of the assignment contract."""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def prose_words(text: str) -> list[str]:
    """The words a reader actually reads.

    A plain `split()` counts every markdown table pipe as a word, so a
    document that uses tables to stay readable is penalised for it. The two
    tables in the report contribute over a hundred such tokens, which is a
    measurement artefact rather than length. Pipes and separator rows are
    dropped before counting.
    """
    without_rules = re.sub(r"^\s*\|[\s|:-]+\|\s*$", "", text, flags=re.M)
    return [word for word in without_rules.replace("|", " ").split() if word]


def test_report_uses_the_seven_required_headings_in_order():
    report = (ROOT / "REPORT.md").read_text(encoding="utf-8")
    required = [
        "## Architecture",
        "## Artifact schema",
        "## Determinism & error handling",
        "## Heterogeneity & multi-tenant",
        "## Escalation & handoff",
        "## Safety",
        "## Cuts",
    ]

    positions = [report.find(heading) for heading in required]
    assert all(position >= 0 for position in positions)
    assert positions == sorted(positions)
    assert len(prose_words(report)) <= 1_500  # keep to the brief's 1-3 pages


def test_readme_documents_setup_and_a_reproducible_demo():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for item in (
        "make install",
        "make run",
        "make discover GOAL=",
        "make replay CAP=",
        "OPENAI_API_KEY",
        "TRANSCRIPT=evidence/discovery-run/transcript.jsonl",
    ):
        assert item in readme


def test_evidence_contains_discovery_replay_failure_and_handoff_runs():
    required_files = (
        "evidence/discovery-run/member.savings_balance@0.1.0.capability.json",
        "evidence/discovery-run/run.jsonl",
        "evidence/discovery-run/transcript.jsonl",
        "evidence/replay-success/result.json",
        "evidence/replay-not-found/result.json",
        "evidence/replay-hard-failure/result.json",
        "evidence/replay-hard-failure/failure.txt",
        "evidence/replay-escalation/result.json",
    )

    missing = [path for path in required_files if not (ROOT / path).is_file()]
    assert not missing, f"missing submission evidence: {missing}"
