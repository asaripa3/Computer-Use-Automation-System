"""Nothing in evidence/ may carry member data.

§3.4 forbids persisting full PII into artifacts or logs. Evidence is the file
set most likely to be attached to a ticket or pasted into a chat, so it is the
one worth checking mechanically rather than trusting.

This caught a real leak: the discovery loop logged the rendered page after
every action, so a member's name appeared eight times in a committed run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

EVIDENCE = Path(__file__).resolve().parents[1] / "evidence"
FILES = sorted(p for p in EVIDENCE.rglob("*") if p.suffix in {".json", ".jsonl", ".txt", ".md"})

# Values from the seeded member data. None of these belong in a run record:
# names and contact details are PII, and the rest identifies an individual.
FORBIDDEN = [
    "Ashworth", "Vandermeer", "Kolbeck", "Brannigan", "Tamsett",
    "4182", "7734", "2205", "6640",
    "555-0142", "555-0198", "555-0177",
    "d.ashworth", "m.pell", "example.invalid",
]

CREDENTIALS = ["Demo-Pass-1234", "svc_agent", "OPENAI_API_KEY", "sk-"]


def test_there_is_evidence_to_check():
    assert FILES, "evidence/ should hold the committed runs"


def test_committed_evidence_does_not_include_unredacted_pixel_captures():
    screenshots = sorted(EVIDENCE.rglob("*.png"))
    assert not screenshots, (
        "pixel captures can contain unstructured personal data; persist the "
        f"redacted page-shape record instead: {screenshots}"
    )


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(EVIDENCE)))
def test_no_member_data_is_written_down(path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    found = [value for value in FORBIDDEN if value in text]
    assert not found, f"{path.relative_to(EVIDENCE)} leaks {found}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(EVIDENCE)))
def test_no_credential_is_written_down(path):
    text = path.read_text(encoding="utf-8", errors="ignore")
    found = [value for value in CREDENTIALS if value in text]
    assert not found, f"{path.relative_to(EVIDENCE)} leaks {found}"


def test_the_discovery_transcript_records_decisions_not_pages():
    """The transcript replays decisions; page content is re-derived by running.

    Keeping it would put member data in the file most likely to be shared.
    """
    import json

    path = EVIDENCE / "discovery-run" / "transcript.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    assert any(r["event"] == "turn" for r in records), "decisions must be recorded"
    for record in records:
        if record.get("event") == "results":
            for result in record["results"]:
                assert "text" not in result, "page content must not be persisted"


def test_a_failure_record_keeps_the_structure_and_drops_the_content():
    """What a locator failure is debugged from is the shape of the page.

    Control names, column headers and refs are the application's own
    vocabulary and are kept. Values, and names that are merely whatever a
    block of text happened to say, are replaced by their length -- because a
    page of a member's record is exactly the regulated data §3.4 says must not
    be written down, and schema-driven redaction cannot catch it: a member's
    name appears on screen without any capability ever declaring it.
    """
    perceived = (EVIDENCE / "replay-hard-failure" / "failure.txt").read_text()

    assert "chars>" in perceived, "values should be recorded by shape"
    assert "cell" in perceived or "text" in perceived, "roles are kept"
    assert "url:" in perceived, "the page is still identified"
