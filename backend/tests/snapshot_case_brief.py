"""Writes the frozen Case Brief context snapshot used by `test_context_compatibility.py`.

Run once, before a refactor of the shared context layer, from `backend`:

    .venv\\Scripts\\python tests\\snapshot_case_brief.py

The snapshot records, for every patient of the two synthetic care managers in a fixed world,
exactly what the Case Brief context service produced: items, model items, reader-note sources,
fingerprint, identifiers, vocabulary, a hash of the withheld texts and the template result.
A later refactor must reproduce it byte for byte (Case Brief behaviour is preserved).
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

SNAPSHOT = HERE / "data" / "case_brief_context_snapshot.json"


def build_world():
    from conftest import new_session

    from app import cycle
    from app.datagen.generate import GenConfig, generate

    db = new_session()
    generate(db, GenConfig(seed=23, n_patients=60, n_hcps=12, n_reps=2, n_care_managers=2))
    cycle.run(db)
    return db


def capture(db) -> dict:
    from sqlalchemy import select

    from app.ai import context
    from app.ai.contracts import ContextRequest
    from app.ai.operations import case_brief
    from app.models import CareManagerPatient, User

    out: dict = {}
    for username in ("cm01", "cm02"):
        user = db.scalar(select(User).where(User.username == username))
        ids = db.scalars(
            select(CareManagerPatient.patient_id)
            .where(CareManagerPatient.care_manager_user_id == user.id)
            .order_by(CareManagerPatient.patient_id)
        ).all()
        for pid in ids:
            request = ContextRequest(operation="case_brief", subject_type="patient", subject_id=pid)
            ctx = context.build(
                db,
                user,
                request,
                permission=case_brief.SPEC.permission,
                allowed_kinds=case_brief.SPEC.context_kinds,
            )
            out[f"{username}:{pid}"] = {
                "as_of": ctx.as_of.isoformat(),
                "fingerprint": ctx.fingerprint,
                "items": [i.model_dump(mode="json", exclude_none=True) for i in ctx.items],
                "model_items": [i.id for i in ctx.model_items],
                "notes": [[n.source, n.source_id] for n in ctx.reader_notes],
                "identifiers": ctx.identifiers,
                "vocabulary": ctx.vocabulary,
                "withheld_sha256": hashlib.sha256(
                    json.dumps(ctx.withheld_texts).encode()
                ).hexdigest(),
                # Synthetic text only (generated world); kept so a refactor can prove the
                # withheld list still starts with exactly these entries.
                "withheld": ctx.withheld_texts,
                "messages": [m.content for m in case_brief.build_messages(ctx)],
                "template": case_brief.fallback(ctx),
            }
            db.rollback()
    return out


if __name__ == "__main__":
    SNAPSHOT.parent.mkdir(exist_ok=True)
    data = capture(build_world())
    SNAPSHOT.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(data)} contexts written to {SNAPSHOT}")
