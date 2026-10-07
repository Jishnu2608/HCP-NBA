"""The representative's commercial loop (MR journey breakpoints MR-1 to MR-15): contacts the
representative proposes through the engine's gates, HCP intent reaching the assigned
representative, follow-ups and meetings, richer outcomes, engine signals, reassignment."""

from datetime import date, datetime, time, timedelta

import pytest
from conftest import ApiClient, cookie_header, new_session, sign_in
from sqlalchemy import select

from app import cycle, pipeline
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.features.population import load_population
from app.main import app
from app.models import Content, HcpTask, Interaction, Nba, RepHcp, User
from app.models.enums import NbaStatus
from app.nba import engine

ALL_OK = {p: {"verdict": "ok", "note": ""} for p in ("medical", "legal", "regulatory")}
PERSONAS = {
    "admin": "admin",
    "mlr": "compliance1",
    "rep": "rep01",
    "other_rep": "rep02",
    "cm": "cm01",
    "hcp": "hcp0002",
    "other_hcp": "hcp0003",
}


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=41, n_patients=200, n_hcps=40, n_reps=4, n_care_managers=3))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    client = ApiClient(app)
    tokens = {role: sign_in(client, name) for role, name in PERSONAS.items()}
    yield client, db, tokens
    app.dependency_overrides.clear()
    db.close()


def call(env, role, method, path, **kw):
    client, _, tokens = env
    return client.request(method, path, headers=cookie_header(nba_session=tokens[role]), **kw)


def code(r) -> str:
    return r.json()["detail"]["code"]


def user_id(env, username) -> int:
    return env[1].scalar(select(User.id).where(User.username == username))


def rep_hcps(env, username="rep01") -> list[str]:
    return list(
        env[1].scalars(select(RepHcp.hcp_id).where(RepHcp.rep_user_id == user_id(env, username)))
    )


def eligible_option(env, hcp_id):
    r = call(env, "rep", "GET", f"/api/hcps/{hcp_id}/contact-options")
    assert r.status_code == 200, r.text
    data = r.json()
    return data, next(
        (o for o in data["options"] if o["eligible"] and o["channel"] in ("email", "portal")),
        None,
    )


def test_rep_proposes_contact_through_the_engine_gates(env):
    _, db, _ = env
    # An assigned HCP the limits allow today, with an approved option.
    for hcp_id in rep_hcps(env):
        data, option = eligible_option(env, hcp_id)
        if data["window"]["allowed_now"] and option:
            break
    else:
        pytest.fail("no contactable HCP in the representative's panel")
    assert all(o["content_id"] for o in data["options"])
    proposed = call(
        env, "rep", "POST", f"/api/hcps/{hcp_id}/contact",
        json={"content_id": option["content_id"], "channel": option["channel"]},
    )  # fmt: skip
    assert proposed.status_code == 201, proposed.text
    nba_id = proposed.json()["id"]
    nba = db.get(Nba, nba_id)
    assert nba.origin == "rep" and nba.status == NbaStatus.READY_FOR_REVIEW
    assert "Proposed by" in nba.rationale
    # The next cycle keeps the representative's proposal.
    assert call(env, "admin", "POST", "/api/admin/cycle").status_code == 200
    db.refresh(nba)
    assert nba.status == NbaStatus.READY_FOR_REVIEW
    # Review and send as usual: governed wording, recorded on the delivery.
    assert call(env, "rep", "POST", f"/api/nba/{nba_id}/approve", json={}).status_code == 200
    assert call(env, "rep", "POST", f"/api/nba/{nba_id}/send").status_code == 200
    sent = db.scalar(select(Interaction).where(Interaction.nba_id == nba_id))
    assert sent.draft_id is not None
    # Now the contact limits apply: the next proposal is refused with the date it is allowed.
    data, _ = eligible_option(env, hcp_id)
    assert not data["window"]["allowed_now"] and data["window"]["next_allowed"]
    again = call(
        env, "rep", "POST", f"/api/hcps/{hcp_id}/contact",
        json={"content_id": option["content_id"], "channel": option["channel"]},
    )  # fmt: skip
    assert again.status_code == 409 and code(again) == "contact_not_allowed"
    assert again.json()["detail"]["next_allowed"]


def test_proposals_refuse_what_the_gates_refuse(env):
    _, db, _ = env
    hcp_id = rep_hcps(env)[0]
    pending = db.scalar(
        select(Content).where(Content.audience == "HCP", Content.mlr_status == "pending")
    )
    r = call(
        env, "rep", "POST", f"/api/hcps/{hcp_id}/contact",
        json={"content_id": pending.content_id, "channel": "email"},
    )  # fmt: skip
    assert r.status_code == 409 and code(r) == "contact_not_allowed"
    elsewhere = next(h for h in rep_hcps(env, "rep02") if h not in rep_hcps(env))
    r = call(env, "rep", "GET", f"/api/hcps/{elsewhere}/contact-options")
    assert r.status_code == 404
    for role in ("cm", "hcp", "mlr"):
        assert call(env, role, "GET", f"/api/hcps/{hcp_id}/contact-options").status_code == 403
        assert call(env, role, "GET", "/api/hcp-work").status_code == 403


def _delivery_to_hcp0002(env) -> Interaction:
    _, db, _ = env
    hcp_id = db.scalar(select(User.hcp_id).where(User.username == "hcp0002"))
    i = db.scalar(
        select(Interaction).where(
            Interaction.target_id == hcp_id,
            Interaction.source == "nba",
            Interaction.channel.in_(("email", "portal")),
        )
    )
    if i is None:
        nba = db.scalar(
            select(Nba).where(
                Nba.target_id == hcp_id,
                Nba.status == NbaStatus.READY_FOR_REVIEW,
                Nba.channel.in_(("email", "portal")),
            )
        )
        call(env, "rep", "POST", f"/api/nba/{nba.id}/approve", json={})
        call(env, "rep", "POST", f"/api/nba/{nba.id}/send")
        i = db.scalar(select(Interaction).where(Interaction.nba_id == nba.id))
    return i


def test_hcp_intent_reaches_the_assigned_rep_and_becomes_a_meeting(env):
    _, db, _ = env
    i = _delivery_to_hcp0002(env)
    assert (
        call(
            env, "other_hcp", "POST", f"/api/me/inbox/{i.id}/intent", json={"intent": "interested"}
        ).status_code
        == 404
    )
    r = call(
        env, "hcp", "POST", f"/api/me/inbox/{i.id}/intent",
        json={"intent": "request_meeting", "note": "Next week please"},
    )  # fmt: skip
    assert r.status_code == 200 and r.json()["intent"] == "request_meeting"
    db.refresh(i)
    assert i.intent == "request_meeting" and i.outcome == "replied"
    work = call(env, "rep", "GET", "/api/hcp-work", params={"view": "requests"}).json()
    request = next(t for t in work["items"] if t["interaction_id"] == i.id)
    assert request["intent"] == "request_meeting" and request["from_hcp"]
    assert call(env, "rep", "GET", "/api/me/attention").json()["hcp_work"] >= 1
    # The HCP asked: a meeting is allowed even inside the contact gap.
    when = (date.today() + timedelta(days=1)).isoformat() + "T10:00"
    meeting = call(
        env, "rep", "POST", "/api/hcp-work",
        json={"hcp_id": i.target_id, "kind": "meeting", "reason": "Requested by the HCP",
              "scheduled_at": when, "timezone": "Europe/London", "mode": "phone",
              "source_task_id": request["id"]},
    )  # fmt: skip
    assert meeting.status_code == 201, meeting.text
    m = meeting.json()
    assert m["status"] == "scheduled" and m["requested_by_hcp"] and m["timezone"] == "Europe/London"
    assert db.get(HcpTask, request["id"]).status == "done"
    # The engine proposes no other outreach while the meeting is open.
    call(env, "admin", "POST", "/api/admin/cycle")
    latest = db.scalar(select(Nba.cycle_id).order_by(Nba.cycle_id.desc()))
    assert not db.scalar(select(Nba).where(Nba.cycle_id == latest, Nba.target_id == i.target_id))
    # Held: logged with a richer outcome and a note; it counts as a contact.
    done = call(
        env, "rep", "PATCH", f"/api/hcp-work/{m['id']}",
        json={"action": "complete", "outcome": "completed", "outcome_reason": "interested",
              "note": "Wants the evidence summary"},
    )  # fmt: skip
    assert done.status_code == 200 and done.json()["status"] == "done"
    held = db.get(Interaction, db.get(HcpTask, m["id"]).interaction_id)
    assert held.source == "rep" and held.outcome_reason == "interested" and held.channel == "phone"
    today = call(env, "rep", "GET", "/api/hcp-work", params={"view": "done_today"}).json()
    assert any(t["id"] == m["id"] for t in today["items"])
    assert any(e["id"] == held.id for e in today["engagements_today"])


def test_follow_ups_and_rep_initiated_meetings_respect_dates_and_limits(env):
    _, db, _ = env
    hcp_id = db.scalar(select(User.hcp_id).where(User.username == "hcp0002"))
    past = call(
        env, "rep", "POST", "/api/hcp-work",
        json={"hcp_id": hcp_id, "kind": "follow_up", "reason": "Send evidence",
              "due_date": (date.today() - timedelta(days=1)).isoformat()},
    )  # fmt: skip
    assert past.status_code == 422
    fu = call(
        env, "rep", "POST", "/api/hcp-work",
        json={"hcp_id": hcp_id, "kind": "follow_up", "reason": "Send evidence",
              "due_date": (date.today() + timedelta(days=3)).isoformat()},
    ).json()  # fmt: skip
    assert fu["status"] == "open" and not fu["overdue"]
    moved = call(
        env, "rep", "PATCH", f"/api/hcp-work/{fu['id']}",
        json={"action": "reschedule", "due_date": (date.today() + timedelta(days=5)).isoformat()},
    )  # fmt: skip
    assert moved.json()["due_date"] == (date.today() + timedelta(days=5)).isoformat()
    no_reason = call(env, "rep", "PATCH", f"/api/hcp-work/{fu['id']}", json={"action": "cancel"})
    assert no_reason.status_code == 422
    # Contacted today (the meeting above): a meeting the rep proposes must wait for the gap.
    soon = (date.today() + timedelta(days=1)).isoformat() + "T09:00"
    gap = call(
        env, "rep", "POST", "/api/hcp-work",
        json={"hcp_id": hcp_id, "kind": "meeting", "reason": "Detail visit",
              "scheduled_at": soon, "timezone": "America/New_York"},
    )  # fmt: skip
    assert gap.status_code == 409 and code(gap) == "contact_gap"
    assert gap.json()["detail"]["next_allowed"]


def test_visit_outcome_reason_and_note(env):
    _, db, _ = env
    nba = db.scalar(
        select(Nba).where(
            Nba.target_type == "HCP",
            Nba.channel == "rep_visit",
            Nba.status == NbaStatus.READY_FOR_REVIEW,
            Nba.target_id.in_(rep_hcps(env)),
        )
    )
    if nba is None:
        pytest.skip("no visit recommendation in this panel")
    call(env, "rep", "POST", f"/api/nba/{nba.id}/approve", json={})
    call(env, "rep", "POST", f"/api/nba/{nba.id}/send")
    r = call(
        env, "rep", "POST", f"/api/nba/{nba.id}/outcome",
        json={"outcome": "completed", "reason": "need_info", "note": "Asked for dosing data"},
    )  # fmt: skip
    assert r.status_code == 200
    i = db.scalar(select(Interaction).where(Interaction.nba_id == nba.id))
    assert i.outcome_reason == "need_info" and i.note == "Asked for dosing data"
    pop = load_population(db)
    assert engine.wants_evidence(pop, nba.target_id)


def test_declined_material_is_not_proposed_again(env):
    _, db, _ = env
    settings = pipeline.engine_settings(db)
    hcp_id = rep_hcps(env)[-1]
    pop = load_population(db)
    state = engine.eng.hcp_state(pop, hcp_id)
    before = {c.content.lineage_id for c in engine.hcp_candidates(pop, hcp_id, state, settings)}
    target = next(iter(before))
    content = db.scalar(select(Content).where(Content.lineage_id == target))
    db.add(
        Interaction(
            target_type="HCP", target_id=hcp_id, channel="email",
            int_ts=datetime.combine(pop.as_of, time(9)), type=content.action_type,
            outcome="declined", content_id=content.content_id, source="nba", intent="decline",
        )
    )  # fmt: skip
    db.flush()
    pop = load_population(db)
    after = {c.content.lineage_id for c in engine.hcp_candidates(pop, hcp_id, state, settings)}
    assert target not in after
    db.rollback()


def test_reassignment_moves_open_work_and_context(env):
    _, db, _ = env
    hcp_id = db.scalar(select(User.hcp_id).where(User.username == "hcp0002"))
    open_task = db.scalar(select(HcpTask).where(HcpTask.hcp_id == hcp_id, HcpTask.status == "open"))
    assert open_task.owner_user_id == user_id(env, "rep01")
    rep02 = user_id(env, "rep02")
    rep01 = user_id(env, "rep01")
    keep = [h for h in rep_hcps(env, "rep01") if h != hcp_id]
    assert (
        call(
            env, "admin", "PUT", f"/api/admin/users/{rep01}/assignments", json={"hcp_ids": keep}
        ).status_code
        == 200
    )
    others = rep_hcps(env, "rep02") + [hcp_id]
    assert (
        call(
            env, "admin", "PUT", f"/api/admin/users/{rep02}/assignments", json={"hcp_ids": others}
        ).status_code
        == 200
    )
    db.refresh(open_task)
    assert open_task.owner_user_id == rep02
    mine = call(env, "rep", "GET", "/api/hcp-work", params={"view": "follow_ups"}).json()["items"]
    assert all(t["hcp_id"] != hcp_id for t in mine)
    theirs = call(env, "other_rep", "GET", "/api/hcp-work", params={"view": "follow_ups"}).json()
    assert any(t["id"] == open_task.id and t["yours"] for t in theirs["items"])
    # History stays readable for the new representative, labelled.
    hcp360 = call(env, "other_rep", "GET", f"/api/hcps/{hcp_id}").json()
    assert any(
        i.get("sent_by") in ("Medical Rep 01",) or i.get("sent_by") for i in hcp360["interactions"]
    )
    assert hcp360["location"] and "contact_window" in hcp360
    assert call(env, "rep", "GET", f"/api/hcps/{hcp_id}").status_code == 404


def test_product_travels_with_the_proposal(env):
    proposal = {
        "title": "Starter pack overview", "body": "What the starter pack contains. Synthetic.",
        "action_type": "program_info", "channels": ["email"], "measure": "diabetes",
        "product": "Synthformin 500 mg", "claims": [{"text": "Pack contents", "reference": "S-9"}],
        "indication": "Adults with type 2 diabetes", "safety_info": "No efficacy claim.",
    }  # fmt: skip
    c = call(env, "rep", "POST", "/api/content", json=proposal).json()
    assert c["product"] == "Synthformin 500 mg"
    call(env, "rep", "POST", f"/api/content/{c['content_id']}/submit")
    approved = call(
        env, "mlr", "POST", f"/api/content/{c['content_id']}/review",
        json={"decision": "approve", **ALL_OK},
    ).json()  # fmt: skip
    assert approved["product"] == "Synthformin 500 mg" and approved["usable"]
    listed = call(env, "rep", "GET", "/api/content").json()
    assert next(x for x in listed if x["content_id"] == c["content_id"])["usable"]
    frozen = call(env, "rep", "PATCH", f"/api/content/{c['content_id']}", json=proposal)
    assert "approved" in frozen.json()["detail"]["message"]


def test_rep_attention_matches_the_queue(env):
    att = call(env, "rep", "GET", "/api/me/attention").json()
    queue = call(env, "rep", "GET", "/api/nba", params={"target_type": "HCP"}).json()
    assert att["outreach"] >= queue["counts"].get("ready_for_review", 0)
    assert "hcp_work" in att
    assert "outreach" not in call(env, "mlr", "GET", "/api/me/attention").json()


def test_demo_population_unaffected():
    db = new_session()
    generate(db, GenConfig(seed=3, n_patients=60, n_hcps=12, n_reps=2, n_care_managers=1))
    assert db.scalar(select(HcpTask)) is None
