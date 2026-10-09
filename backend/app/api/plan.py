"""Health goal plans (`clinical.plan`).

Care manager, patients on their panel (`patient:care:manage`):
  GET  /api/care/patients/{id}/plan      plan, catalogue, readings, check-ins
  PUT  /api/care/patients/{id}/plan      create or replace the plan (real patients only)
Patient, own record (`self:health:manage`):
  GET  /api/me/plan                      plan, today's progress, who sets it
  POST /api/me/readings                  record a reading of a tracked measurement
  POST /api/me/checkins/medication       the daily medicines confirmation, if in the plan
  GET  /api/me/coins                     balance, history, badges
  GET  /api/me/board, PUT /api/me/board  the opt-in monthly board (alias and country only)
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.care import _patient
from app.api.deps import require_permission
from app.api.me import own_patient_id
from app.api.schemas import StrictBody
from app.clinical import care, plan, records
from app.core import clock
from app.core.db import get_db
from app.core.permissions import Permission
from app.models import Patient, User
from app.models.tables import utcnow

router = APIRouter(prefix="/api", tags=["care plan"])
managers = require_permission(Permission.PATIENT_CARE_MANAGE)
patients = require_permission(Permission.SELF_HEALTH_MANAGE)


class Target(StrictBody):
    low: float | None = None
    high: float | None = None


class PlanMeasure(StrictBody):
    key: str = Field(max_length=32)
    targets: dict[str, Target] | None = None


class PlanBody(StrictBody):
    measures: list[PlanMeasure] = Field(default_factory=list, max_length=len(plan.MEASURES))
    medication_check: bool = False
    checkin_quota: int


class ReadingBody(StrictBody):
    measure: str = Field(max_length=32)
    values: dict[str, float]
    taken_at: datetime | None = None


class BoardBody(StrictBody):
    opted_in: bool


def _detail(db: Session, patient_id: str) -> dict:
    return {
        "plan": plan.plan_out(db, patient_id),
        "progress": plan.today_progress(db, patient_id),
        "readings": plan.readings_series(db, patient_id, utcnow()),
        "calendar": plan.checkin_calendar(db, patient_id, clock.get_today(db)),
    }


@router.get("/care/patients/{patient_id}/plan")
def get_plan(
    patient_id: str, user: User = Depends(managers), db: Session = Depends(get_db)
) -> dict:
    _patient(db, user, patient_id)
    return {"catalogue": plan.catalogue(), **_detail(db, patient_id)}


@router.put("/care/patients/{patient_id}/plan")
def put_plan(
    patient_id: str, body: PlanBody, user: User = Depends(managers), db: Session = Depends(get_db)
) -> dict:
    patient = _patient(db, user, patient_id)
    care.require_real(patient)
    plan.set_plan(
        db,
        user,
        patient,
        {
            "measures": [
                {"key": m.key, "targets": {k: t.model_dump() for k, t in (m.targets or {}).items()}}
                for m in body.measures
            ],
            "medication_check": body.medication_check,
            "checkin_quota": body.checkin_quota,
        },
    )
    db.commit()
    return {"catalogue": plan.catalogue(), **_detail(db, patient_id)}


@router.get("/me/plan")
def my_plan(user: User = Depends(patients), db: Session = Depends(get_db)) -> dict:
    pid = own_patient_id(user)
    managers_ = records.responsible_care_managers(db, pid, active_only=True)
    return {
        **_detail(db, pid),
        "care_managers": [m.display_name for m in managers_],
        "has_care_manager": bool(managers_),
        "medication_label": plan.MEDICATION_LABEL,
    }


@router.post("/me/readings", status_code=201)
def add_reading(
    body: ReadingBody, user: User = Depends(patients), db: Session = Depends(get_db)
) -> dict:
    pid = own_patient_id(user)
    taken = body.taken_at.replace(tzinfo=None) if body.taken_at else None
    reading = plan.record_reading(db, user, pid, body.measure, body.values, taken)
    db.commit()
    return {
        "reading": reading,
        "progress": plan.today_progress(db, pid),
        "coins": plan.coins(db, pid),
    }


@router.post("/me/checkins/medication", status_code=201)
def confirm_medication(user: User = Depends(patients), db: Session = Depends(get_db)) -> dict:
    pid = own_patient_id(user)
    plan.confirm_medication(db, user, pid)
    db.commit()
    return {"progress": plan.today_progress(db, pid), "coins": plan.coins(db, pid)}


@router.get("/me/coins")
def my_coins(user: User = Depends(patients), db: Session = Depends(get_db)) -> dict:
    return plan.coins(db, own_patient_id(user))


@router.get("/me/board")
def my_board(user: User = Depends(patients), db: Session = Depends(get_db)) -> dict:
    return plan.board(db, own_patient_id(user))


@router.put("/me/board")
def set_board(
    body: BoardBody, user: User = Depends(patients), db: Session = Depends(get_db)
) -> dict:
    pid = own_patient_id(user)
    if db.get(Patient, pid) is None:
        raise plan._bad("no_record", "No patient record.", 404)
    out = plan.set_board(db, user, pid, body.opted_in)
    db.commit()
    return out
