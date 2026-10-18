"""Case Brief compatibility: the refactored context layer reproduces the frozen snapshot.

`tests/data/case_brief_context_snapshot.json` was written by `tests/snapshot_case_brief.py`
from the code at commit 51abf0a, before the shared context layer was generalised. For every
patient of both synthetic care managers the new engine must give the same items (ids, kinds,
text, provenance, dates), the same model items, reader notes, fingerprint, vocabulary, prompt
messages and template, and identifier and withheld lists that start with, or contain, exactly
the historical entries.
"""

import json
from pathlib import Path

import pytest
from snapshot_case_brief import build_world, capture

SNAPSHOT = Path(__file__).parent / "data" / "case_brief_context_snapshot.json"


@pytest.fixture(scope="module")
def frozen():
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def current(frozen):
    # The snapshot was taken on one day; the world is rebuilt as of that same day, so the
    # comparison does not depend on the date the suite runs (every context carries `as_of`).
    from datetime import date

    from app.core import clock

    (taken,) = {entry["as_of"] for entry in frozen.values()}
    with clock.override(date.fromisoformat(taken)):
        db = build_world()
        try:
            captured = capture(db)
        finally:
            db.close()
    return captured


def test_the_snapshot_covers_both_care_managers(frozen):
    assert len(frozen) == 60
    assert {k.split(":")[0] for k in frozen} == {"cm01", "cm02"}


def test_every_case_brief_context_is_reproduced_exactly(current, frozen):
    assert set(current) == set(frozen)
    for key, old in frozen.items():
        new = current[key]
        for field in (
            "as_of", "fingerprint", "items", "model_items", "notes", "vocabulary", "messages",
            "template",
        ):  # fmt: skip
            assert new[field] == old[field], f"{key}: {field} changed"


def test_identifiers_and_withheld_text_only_grow(current, frozen):
    for key, old in frozen.items():
        new = current[key]
        assert set(old["identifiers"]) <= set(new["identifiers"]), key
        assert new["withheld"][: len(old["withheld"])] == old["withheld"], key
