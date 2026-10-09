"""The MLR content lifecycle (MLR journey breakpoints M-01 to M-18): proposal, review with
medical / legal / regulatory perspectives, request changes, new versions, approval that
supersedes, withdrawal that propagates, governed wording, conversations, delivery oversight,
reviewer ownership and a demo reset that keeps governance."""

from datetime import date

import pytest
from conftest import ApiClient, cookie_header, new_session, sign_in
from sqlalchemy import select

from app import cycle
from app.content import governance
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import AuditLog, Content, ContentReview, Interaction, MessageDraft, Nba, User
from app.models.enums import MlrStatus, NbaStatus, Role
from app.nba import gates

ALL_OK = {p: {"verdict": "ok", "note": ""} for p in ("medical", "legal", "regulatory")}
PERSONAS = {
    "admin": "admin",
    "mlr": "compliance1",
    "mlr2": "compliance2",
    "rep": "rep01",
    "other_rep": "rep02",
    "cm": "cm01",
    "hcp": "hcp0002",
    "other_hcp": "hcp0003",
}
PROPOSAL = {
    "title": "Adherence conversations in type 2 diabetes",
    "body": "Three short questions that help patients say what makes their medication hard to "
    "keep taking. Illustrative synthetic material.",
    "action_type": "hcp_education",
    "channels": ["email", "portal"],
    "measure": "diabetes",
}
FACTS = {
    "claims": [
        {"text": "Asking about barriers improves disclosure", "reference": "Synthetic study S-1"}
    ],
    "indication": "Adults with type 2 diabetes on long-term medication",
    "safety_info": "No product claim; patients follow their prescriber's advice.",
    "jurisdictions": ["US"],
}


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=31, n_patients=200, n_hcps=40, n_reps=4, n_care_managers=3))
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


def code(response) -> str:
    return response.json()["detail"]["code"]


def attention(env, role) -> dict:
    return call(env, role, "GET", "/api/me/attention").json()


def submitted_proposal(env) -> dict:
    draft = call(env, "rep", "POST", "/api/content", json={**PROPOSAL, **FACTS}).json()
    done = call(env, "rep", "POST", f"/api/content/{draft['content_id']}/submit")
    assert done.status_code == 200, done.text
    return done.json()


def test_propose_review_request_changes_revise_approve(env):
    _, db, _ = env
    # A draft without the facts MLR needs cannot be submitted.
    draft = call(env, "rep", "POST", "/api/content", json=PROPOSAL)
    assert draft.status_code == 201 and draft.json()["mlr_status"] == "draft"
    cid = draft.json()["content_id"]
    incomplete = call(env, "rep", "POST", f"/api/content/{cid}/submit")
    assert incomplete.status_code == 422 and code(incomplete) == "incomplete_submission"
    # The draft is private to its author.
    assert call(env, "mlr", "GET", f"/api/content/{cid}").status_code == 404
    assert call(env, "other_rep", "GET", f"/api/content/{cid}").status_code == 404
    call(env, "rep", "PATCH", f"/api/content/{cid}", json={**PROPOSAL, **FACTS})
    before = attention(env, "mlr").get("content", 0)
    sub = call(env, "rep", "POST", f"/api/content/{cid}/submit")
    assert sub.json()["mlr_status"] == "pending" and sub.json()["status_label"].startswith("Sub")
    assert attention(env, "mlr")["content"] == before + 1

    # MLR: approval needs all three perspectives; sending back needs feedback for the author.
    review = f"/api/content/{cid}/review"
    no_views = call(env, "mlr", "POST", review, json={"decision": "approve"})
    assert code(no_views) == "perspectives_incomplete"
    no_feedback = call(env, "mlr", "POST", review, json={"decision": "request_changes"})
    assert code(no_feedback) == "feedback_required"
    concern = {**ALL_OK, "legal": {"verdict": "concern", "note": "Benefit wording overstated"}}
    blocked = call(env, "mlr", "POST", review, json={"decision": "approve", **concern})
    assert code(blocked) == "concern_open"
    sent_back = call(
        env, "mlr", "POST", review,
        json={
            "decision": "request_changes", **concern, "expected_status": "pending",
            "feedback": "Soften the benefit statement and add the safety sentence.",
            "comment": "Internal: check with legal next week.",
        },
    )  # fmt: skip
    assert sent_back.json()["mlr_status"] == "changes_requested"

    # The author is told, reads the feedback but not the internal comment, and the count clears.
    assert attention(env, "rep")["content"] >= 1
    seen = call(env, "rep", "GET", f"/api/content/{cid}").json()
    last = seen["reviews"][-1]
    assert last["feedback"].startswith("Soften") and last["comment"] is None
    assert last["legal"]["verdict"] == "concern" and last["reviewer"] == "MLR reviewer"
    assert attention(env, "rep").get("content", 0) == 0

    # The reviewed version is frozen; the author opens version 2.
    frozen = call(env, "rep", "PATCH", f"/api/content/{cid}", json={**PROPOSAL, **FACTS})
    assert code(frozen) == "not_editable"
    v2 = call(env, "rep", "POST", f"/api/content/{cid}/revise").json()
    assert v2["version"] == 2 and v2["previous_id"] == cid and v2["mlr_status"] == "draft"
    again = call(env, "rep", "POST", f"/api/content/{cid}/revise")
    assert code(again) == "revision_open"
    softer = {**PROPOSAL, **FACTS, "body": PROPOSAL["body"] + " Results vary."}
    call(env, "rep", "PATCH", f"/api/content/{v2['content_id']}", json=softer)
    call(env, "rep", "POST", f"/api/content/{v2['content_id']}/submit")

    detail = call(env, "mlr", "GET", f"/api/content/{v2['content_id']}").json()
    assert [c["field"] for c in detail["changes"]] == ["body"]
    assert [v["version"] for v in detail["versions"]] == [1, 2]
    assert detail["reviews"][0]["comment"] == "Internal: check with legal next week."
    ok = call(
        env, "mlr", "POST", f"/api/content/{v2['content_id']}/review",
        json={"decision": "approve", **ALL_OK, "expected_status": "pending",
              "expected_version": 2},
    )  # fmt: skip
    assert ok.json()["mlr_status"] == "approved" and ok.json()["usable"]
    twice = call(
        env, "mlr", "POST", f"/api/content/{v2['content_id']}/review",
        json={"decision": "approve", **ALL_OK},
    )  # fmt: skip
    assert code(twice) == "already_approved"
    assert v2["content_id"] in {
        c["content_id"] for c in call(env, "rep", "GET", "/api/content").json()
    }

    # Audit: who (durable id, recognisable as "you"), which version, before and after.
    rows = call(
        env, "mlr", "GET", "/api/audit", params={"entity_id": v2["content_id"], "mine": True}
    ).json()["items"]
    approved = next(r for r in rows if r["action"] == "content_approved")
    assert approved["actor_is_you"] and approved["actor_user_id"]
    assert approved["detail"]["version"] == 2
    assert approved["detail"]["previous_status"] == "pending"
    assert approved["detail"]["resulting_status"] == "approved"
    stored = db.scalar(select(ContentReview).where(ContentReview.content_id == v2["content_id"]))
    assert stored.content_version == 2 and stored.medical == {"verdict": "ok", "note": ""}


def test_new_version_supersedes_old_and_engine_stops_using_it(env):
    _, db, _ = env
    nba = db.scalar(
        select(Nba).where(
            Nba.target_type == "HCP",
            Nba.status == NbaStatus.READY_FOR_REVIEW,
            Nba.content_id.in_(
                select(Content.content_id).where(
                    Content.origin == "library", Content.mlr_status == "approved"
                )
            ),
        )
    )
    old = db.get(Content, nba.content_id)
    # Library material is maintained by MLR: the reviewer opens and submits the new version.
    v2 = call(env, "mlr", "POST", f"/api/content/{old.content_id}/revise").json()
    body = {
        "title": old.title, "body": old.body + " Updated wording.",
        "action_type": old.action_type, "channels": old.channels, "measure": old.measure,
        "specialty": old.specialty, "claims": old.claims, "indication": old.indication,
        "safety_info": old.safety_info,
    }  # fmt: skip
    assert (
        call(env, "mlr", "PATCH", f"/api/content/{v2['content_id']}", json=body).status_code == 200
    )
    call(env, "mlr", "POST", f"/api/content/{v2['content_id']}/submit")
    call(
        env, "mlr", "POST", f"/api/content/{v2['content_id']}/review",
        json={"decision": "approve", **ALL_OK},
    )  # fmt: skip
    db.refresh(old)
    db.refresh(nba)
    assert old.mlr_status == MlrStatus.SUPERSEDED
    assert nba.status == NbaStatus.EXPIRED and "superseded" in nba.block_reason
    # Reps no longer get it as usable material (they may still see it as delivered history).
    assert not any(
        c["content_id"] == old.content_id and c["usable"]
        for c in call(env, "rep", "GET", "/api/content").json()
    )
    assert call(env, "admin", "POST", "/api/admin/cycle").status_code == 200
    latest = db.scalar(select(Nba.cycle_id).order_by(Nba.cycle_id.desc()))
    assert not db.scalar(
        select(Nba).where(Nba.cycle_id == latest, Nba.content_id == old.content_id)
    )
    superseded = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "content_superseded", AuditLog.entity_id == old.content_id
        )
    )
    assert superseded.detail["superseded_by"] == v2["content_id"]
    assert nba.id in superseded.detail["recommendations_expired"]


def test_wording_is_governed_and_the_delivery_records_it(env):
    _, db, _ = env
    nba = db.scalar(
        select(Nba).where(
            Nba.target_id == "HCP_0002",
            Nba.status == NbaStatus.READY_FOR_REVIEW,
            Nba.channel.in_(("email", "portal")),
        )
    ) or db.scalar(
        select(Nba).where(
            Nba.target_id.in_(("HCP_0001", "HCP_0002", "HCP_0003")),
            Nba.status == NbaStatus.READY_FOR_REVIEW,
            Nba.channel.in_(("email", "portal")),
        )
    )
    drafts = call(env, "rep", "GET", f"/api/nba/{nba.id}").json()["drafts"]
    edit = call(
        env, "rep", "PATCH", f"/api/nba/{nba.id}/drafts/{drafts[0]['id']}",
        json={"subject": "New", "body": "Dr, this drug cures cancer and has no side effects."},
    )  # fmt: skip
    assert edit.status_code == 409 and code(edit) == "governed_wording"
    assert "propose a new version" in edit.json()["detail"]["message"]
    # Wording changed behind the API is caught at send.
    tampered = db.get(MessageDraft, drafts[1]["id"])
    tampered.body, tampered.edited_by_user_id = "Unreviewed claim.", 1
    db.commit()
    approve = call(env, "rep", "POST", f"/api/nba/{nba.id}/approve", json={"draft_id": tampered.id})
    assert approve.status_code == 200
    send = call(env, "rep", "POST", f"/api/nba/{nba.id}/send")
    assert (
        send.status_code == 409 and "edited after MLR approval" in send.json()["detail"]["message"]
    )
    # With the governed variant it goes out, and the delivery names the wording sent.
    db.get(MessageDraft, drafts[0]["id"]).is_selected = True
    tampered.is_selected = False
    db.commit()
    assert call(env, "rep", "POST", f"/api/nba/{nba.id}/send").status_code == 200
    delivered = db.scalar(select(Interaction).where(Interaction.nba_id == nba.id))
    assert delivered.draft_id == drafts[0]["id"] and delivered.content_id == nba.content_id


def _delivered_to_hcp(env) -> Interaction:
    _, db, _ = env
    hcp = db.scalar(select(User).where(User.username == "hcp0002")).hcp_id
    i = db.scalar(
        select(Interaction).where(
            Interaction.target_id == hcp,
            Interaction.source == "nba",
            Interaction.channel.in_(("email", "portal")),
        )
    )
    if i is None:
        nba = db.scalar(
            select(Nba).where(
                Nba.target_id == hcp,
                Nba.status == NbaStatus.READY_FOR_REVIEW,
                Nba.channel.in_(("email", "portal")),
            )
        )
        call(env, "admin", "POST", f"/api/nba/{nba.id}/approve", json={})
        call(env, "admin", "POST", f"/api/nba/{nba.id}/send")
        i = db.scalar(select(Interaction).where(Interaction.nba_id == nba.id))
    return i


def test_hcp_question_reaches_mlr_masked_and_the_reply_returns(env):
    _, db, _ = env
    i = _delivered_to_hcp(env)
    q = call(
        env, "hcp", "POST", f"/api/me/inbox/{i.id}/question",
        json={"body": "Does this apply to patients over 80?"},
    )  # fmt: skip
    assert q.status_code == 201 and q.json()["conversation"][0]["author"] == "You"
    other = call(env, "other_hcp", "POST", f"/api/me/inbox/{i.id}/question", json={"body": "Mine?"})
    assert other.status_code == 404
    assert attention(env, "mlr")["content"] >= 1
    detail = call(env, "mlr", "GET", f"/api/content/{i.content_id}").json()
    thread = detail["conversation"]["hcp_threads"][0]
    assert thread["hcp"]["label"].startswith("HCP #")
    hcp_name = db.scalar(select(User).where(User.username == "hcp0002")).display_name
    assert hcp_name not in str(thread)
    reply = call(
        env, "mlr", "POST", f"/api/content/{i.content_id}/messages",
        json={"body": "Yes, within the approved indication.", "interaction_id": i.id},
    )  # fmt: skip
    assert reply.status_code in (200, 201)
    # The reply is counted on the HCP's representative tab, not with patient work.
    assert attention(env, "hcp")["rep_messages"] >= 1
    assert "messages" not in attention(env, "hcp")
    inbox = call(env, "hcp", "GET", "/api/me/inbox").json()
    item = next(x for x in inbox if x["id"] == i.id)
    assert item["conversation"][-1]["author"] == "Medical, legal and regulatory review"
    # The representative who sent it sees the thread with the HCP's name.
    if i.actor_user_id == db.scalar(select(User.id).where(User.username == "rep01")):
        rep_view = call(env, "rep", "GET", f"/api/content/{i.content_id}").json()
        assert rep_view["conversation"]["hcp_threads"][0]["hcp"]["label"] == hcp_name


def test_withdrawal_propagates_and_mlr_sees_deliveries(env):
    _, db, _ = env
    i = _delivered_to_hcp(env)
    c = db.get(Content, i.content_id)
    open_nbas = [
        n.id
        for n in db.scalars(
            select(Nba).where(Nba.content_id == c.content_id, Nba.status.in_(governance.OPEN_NBA))
        )
    ]
    url = f"/api/content/{c.content_id}/review"
    assert code(call(env, "mlr", "POST", url, json={"decision": "withdraw"})) == "feedback_required"
    done = call(
        env, "mlr", "POST", url, json={"decision": "withdraw", "feedback": "Evidence updated."}
    )
    assert done.json()["mlr_status"] == "withdrawn"
    for nid in open_nbas:
        assert db.get(Nba, nid).status == NbaStatus.EXPIRED
    inbox = call(env, "hcp", "GET", "/api/me/inbox").json()
    notice = next(x for x in inbox if x["id"] == i.id)["content_notice"]
    assert notice.startswith("Withdrawn") and "Evidence updated." in notice
    report = done.json()["deliveries"]
    assert report["sent"] >= 1 and report["still_in_inboxes_after_withdrawal"] >= 1
    assert all(it["recipient"]["label"].startswith(("HCP #", "Patient")) for it in report["items"])
    approve_old = call(env, "mlr", "POST", url, json={"decision": "approve", **ALL_OK})
    assert code(approve_old) == "withdrawn_final"


def test_expired_and_rejected_are_not_reapproved_but_can_be_revised(env):
    _, db, _ = env
    expired = db.scalar(
        select(Content).where(Content.mlr_status == "approved", Content.expiry_date <= date.today())
    )
    if expired is not None:
        r = call(
            env, "mlr", "POST", f"/api/content/{expired.content_id}/review",
            json={"decision": "approve", **ALL_OK},
        )  # fmt: skip
        assert code(r) == "expired_needs_revision"
    rejected = db.scalar(select(Content).where(Content.mlr_status == "rejected"))
    r = call(
        env, "mlr", "POST", f"/api/content/{rejected.content_id}/review",
        json={"decision": "approve", **ALL_OK},
    )  # fmt: skip
    assert code(r) == "rejected_final" and "new version" in r.json()["detail"]["message"]
    v2 = call(env, "mlr", "POST", f"/api/content/{rejected.content_id}/revise")
    assert v2.status_code == 201 and v2.json()["version"] == rejected.version + 1


def test_stale_decisions_and_review_ownership(env):
    _, db, _ = env
    c = submitted_proposal(env)
    url = f"/api/content/{c['content_id']}"
    assert call(env, "mlr", "POST", f"{url}/claim-review", json={}).status_code == 200
    owned = call(
        env, "mlr2", "POST", f"{url}/review",
        json={"decision": "reject", "feedback": "Out of scope."},
    )  # fmt: skip
    assert code(owned) == "review_owned"
    stale = call(
        env, "mlr2", "POST", f"{url}/review",
        json={"decision": "reject", "feedback": "x", "expected_status": "changes_requested"},
    )  # fmt: skip
    assert code(stale) == "stale_decision"
    took = call(
        env, "mlr2", "POST", f"{url}/review",
        json={"decision": "reject", "feedback": "Out of scope for HCP material.",
              "take_over": True},
    )  # fmt: skip
    assert took.json()["mlr_status"] == "rejected"
    actions = [
        a.action for a in db.scalars(select(AuditLog).where(AuditLog.entity_id == c["content_id"]))
    ]
    assert "content_review_started" in actions and "content_review_taken_over" in actions


def test_jurisdiction_gate():
    c = Content(
        content_id="X", audience="HCP", channels=["email"], mlr_status="approved",
        effective_date=date(2020, 1, 1), jurisdictions=["GB"],
    )  # fmt: skip
    day = date(2026, 1, 1)
    assert "jurisdiction_mismatch" in gates.content_failures(c, "HCP", "email", day, "US")
    assert gates.content_failures(c, "HCP", "email", day, "GB") == []


def test_roles_cannot_step_outside_content_duties(env):
    for role in ("cm", "hcp", "admin", "mlr"):
        assert (
            call(env, role, "POST", "/api/content", json={**PROPOSAL, **FACTS}).status_code == 403
        )
    pending = submitted_proposal(env)
    for role in ("rep", "admin", "cm"):
        r = call(
            env, role, "POST", f"/api/content/{pending['content_id']}/review",
            json={"decision": "approve", **ALL_OK},
        )  # fmt: skip
        assert r.status_code == 403
    assert (
        call(env, "other_rep", "POST", f"/api/content/{pending['content_id']}/revise").status_code
        == 404
    )
    assert call(env, "mlr", "GET", "/api/patients").status_code == 403
    assert call(env, "mlr", "GET", "/api/hcps").status_code == 403


def test_demo_reset_keeps_governance_and_seeds_no_reviewer():
    db = new_session()
    generate(db, GenConfig(seed=7, n_patients=60, n_hcps=12, n_reps=2, n_care_managers=1))
    assert not db.scalar(select(User).where(User.role == Role.COMPLIANCE))
    rep = db.scalar(select(User).where(User.username == "rep01"))
    reviewer = User(
        username="mlr@example.test", email="mlr@example.test", display_name="Reviewer",
        password_hash="x", role=Role.COMPLIANCE, verified=True, status="active",
        source="invitation",
    )  # fmt: skip
    db.add(reviewer)
    db.flush()
    c = governance.create_draft(db, rep, {**PROPOSAL, **FACTS})
    governance.submit(db, rep, c)
    governance.decide(db, reviewer, c, decision="request_changes", feedback="Add risk text.")
    cid = c.content_id
    db.commit()
    generate(db, GenConfig(seed=7, n_patients=60, n_hcps=12, n_reps=2, n_care_managers=1))
    kept = db.get(Content, cid)
    review = db.scalar(select(ContentReview).where(ContentReview.content_id == cid))
    new_reviewer = db.scalar(select(User).where(User.email == "mlr@example.test"))
    assert kept.mlr_status == "changes_requested" and kept.author_user_id == db.scalar(
        select(User.id).where(User.username == "rep01")
    )
    assert review.reviewer_user_id == new_reviewer.id and review.feedback == "Add risk text."
    assert (
        db.scalar(
            select(AuditLog).where(AuditLog.action == "content_changes_requested")
        ).actor_user_id
        == new_reviewer.id
    )
    assert [u.username for u in db.scalars(select(User).where(User.role == Role.COMPLIANCE))] == [
        "mlr@example.test"
    ]
