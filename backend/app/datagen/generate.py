"""Deterministic synthetic data generator.

Everything is derived from the seed. Each person has their own random stream, so changing
the scale or a hero record does not reshuffle everyone else.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from random import Random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.provisioning import assignments
from app.auth.service import ensure_system_admin
from app.auth.sessions import sessions
from app.clinical import activity, records
from app.clinical import hcps as hcps_module
from app.core import clock
from app.core.config import get_settings
from app.core.db import Base
from app.core.permissions import PROFESSIONAL_ROLES
from app.core.security import hash_password
from app.datagen import behavior
from app.datagen import reference as ref
from app.datagen.content import build_content, valid_on
from app.datagen.heroes import HCP_HEROES, PATIENT_HEROES
from app.models import (
    CareManagerPatient,
    Consent,
    Content,
    Hcp,
    HcpSpecialty,
    Interaction,
    MedicationFill,
    Patient,
    PatientHcp,
    PatientTherapy,
    RepHcp,
    SimLatent,
    User,
)
from app.models.enums import (
    AccountSource,
    AccountStatus,
    ActionType,
    Channel,
    ConsentPurpose,
    Measure,
    Outcome,
    Role,
    TargetType,
    VerificationSource,
)
from app.models.tables import utcnow

# Mix of the pre-engine "blast" outreach programme. Random on purpose: it gives the
# propensity models unbiased history to learn from.
HISTORY_ACTIONS = [
    (ActionType.REFILL_NUDGE, 0.65),
    (ActionType.EDUCATION, 0.15),
    (ActionType.CHECK_IN, 0.12),
    (ActionType.COST_SUPPORT, 0.08),
]
DIGITAL_OUTCOMES = [(Outcome.OPENED, 0.55), (Outcome.CLICKED, 0.35), (Outcome.REPLIED, 0.10)]


@dataclass
class GenConfig:
    seed: int = 20260101
    n_patients: int = 3000
    n_hcps: int = 300
    n_reps: int = 20
    n_care_managers: int = 10
    # Synthetic history ends on the day it is generated (the real date by default) and
    # keeps those historical dates afterwards.
    as_of: date = field(default_factory=clock.today)
    # Demo accounts for the seeded personas. Off = only the fixed administrator is seeded.
    seed_demo_accounts: bool = field(default_factory=lambda: get_settings().seed_demo_accounts)
    # None = read NBA_DEMO_PASSWORD when demo accounts are actually built.
    demo_password: str | None = None


def _past(moment: datetime) -> datetime:
    """Generated history never lies in the future: an event drawn for later today is stamped
    at the start of today (a fixed bound, so generation stays deterministic)."""
    return min(moment, datetime.combine(clock.today(), time(0, 0)))


def _rng(cfg: GenConfig, *parts) -> Random:
    return Random(":".join(str(p) for p in (cfg.seed, *parts)))


def _at(day: date, rng: Random) -> datetime:
    return _past(datetime.combine(day, time(rng.randint(9, 17), rng.randint(0, 59))))


def hcp_key(i: int) -> str:
    return f"HCP_{i:04d}"


def patient_key(i: int) -> str:
    return f"PAT_{i:05d}"


def wipe(db: Session) -> None:
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    # Rows were deleted in bulk; drop any stale objects the session still holds.
    db.expunge_all()


# --- HCPs --------------------------------------------------------------------


def build_hcps(cfg: GenConfig) -> tuple[list[Hcp], dict[str, dict]]:
    hcps, traits = [], {}
    for i in range(1, cfg.n_hcps + 1):
        rng = _rng(cfg, "hcp", i)
        hero = HCP_HEROES.get(i, {})
        specialty, taxonomy, _, median_rx = behavior.weighted_choice(
            rng, [(s, s[2]) for s in ref.SPECIALTIES]
        )
        if hero:
            specialty, taxonomy, _, median_rx = next(
                s for s in ref.SPECIALTIES if s[0] == hero["specialty"]
            )
        state, city, zip3 = ref.GEOGRAPHY[0] if hero else rng.choice(ref.GEOGRAPHY)
        first = rng.choice(ref.FIRST_NAMES_F + ref.FIRST_NAMES_M)
        hcp = Hcp(
            hcp_id=hcp_key(i),
            # Real NPIs start with 1 or 2; a leading 9 guarantees no clash with a real provider.
            npi=f"9{i:05d}{rng.randrange(10**4):04d}",
            first_name=hero.get("first_name", first),
            last_name=hero.get("last_name", rng.choice(ref.LAST_NAMES)),
            specialty=specialty,
            taxonomy_code=taxonomy,
            organization=rng.choice(ref.ORGANIZATIONS).format(city=city),
            city=city,
            state=state,
            country="US",
            zip=f"{zip3}{rng.randrange(100):02d}",
            rx_volume_annual=hero.get("rx_volume", int(median_rx * rng.lognormvariate(0, 0.5))),
        )
        hcps.append(hcp)
        traits[hcp.hcp_id] = hero.get("traits") or behavior.make_hcp_traits(rng, specialty)
    return hcps, traits


def build_hcp_history(
    cfg: GenConfig, hcps: list[Hcp], traits: dict[str, dict], contents: list[Content]
) -> list[Interaction]:
    hcp_content = [c for c in contents if c.audience == TargetType.HCP]
    channel_weight = {"email": 0.55, "portal": 0.17, "rep_visit": 0.28}
    rows: list[Interaction] = []
    for i, hcp in enumerate(hcps, start=1):
        rng = _rng(cfg, "hcp_history", i)
        hero = HCP_HEROES.get(i, {})
        days = sorted(rng.sample(range(1, 366), rng.randint(6, 14)), reverse=True)
        if hero.get("replace_history"):
            days = []
        touched: list[date] = []
        for days_ago in days:
            day = cfg.as_of - timedelta(days=days_ago)
            options = [c for c in hcp_content if valid_on(c, day)]
            if not options:
                continue
            content = rng.choice(options)
            channel = behavior.weighted_choice(
                rng, [(ch, channel_weight[ch]) for ch in content.channels]
            )
            recent = sum(1 for d in touched if (day - d).days <= 30)
            touched.append(day)
            p = behavior.hcp_engage_prob(traits[hcp.hcp_id], channel, content.topic, recent)
            ts = _at(day, rng)
            if rng.random() < p:
                outcome = (
                    Outcome.COMPLETED
                    if channel == Channel.REP_VISIT
                    else behavior.weighted_choice(rng, DIGITAL_OUTCOMES)
                )
                outcome_ts = _past(ts + timedelta(hours=rng.randint(0, 30)))
            else:
                outcome = Outcome.DECLINED if channel == Channel.REP_VISIT else Outcome.NO_RESPONSE
                outcome_ts = None
            rows.append(
                Interaction(
                    target_type=TargetType.HCP,
                    target_id=hcp.hcp_id,
                    channel=channel,
                    int_ts=ts,
                    type=content.action_type,
                    outcome=outcome,
                    outcome_ts=outcome_ts,
                    content_id=content.content_id,
                )
            )
        for days_ago, channel, topic, action, outcome in hero.get("history", []):
            day = cfg.as_of - timedelta(days=days_ago)
            content = next(
                c
                for c in hcp_content
                if c.topic == topic
                and c.action_type == action
                and channel in c.channels
                and valid_on(c, day)
            )
            ts = _at(day, rng)
            quiet = outcome in (Outcome.NO_RESPONSE, Outcome.DECLINED)
            rows.append(
                Interaction(
                    target_type=TargetType.HCP,
                    target_id=hcp.hcp_id,
                    channel=channel,
                    int_ts=ts,
                    type=content.action_type,
                    outcome=outcome,
                    outcome_ts=None if quiet else _past(ts + timedelta(hours=3)),
                    content_id=content.content_id,
                )
            )
    return rows


# --- Patients ----------------------------------------------------------------


@dataclass
class TherapySpec:
    measure: str
    drug: str
    rxnorm: str | None
    days_supply: int
    copay: float
    prescriber: str
    start: date
    # Hero-only: explicit fill dates and unprompted next fill instead of simulation.
    fixed_fills: list[date] | None = None
    fixed_next_fill: date | None = None


@dataclass
class PatientBundle:
    patient: Patient
    traits: dict
    primary_hcp: str
    therapies: list[TherapySpec]
    consents: list[Consent]
    fixed_history: list[tuple] | None = None


def _rxnorm(drug: str) -> str | None:
    return next((code for drugs in ref.DRUGS.values() for n, code, _ in drugs if n == drug), None)


def _consent_rows(patient_id, granted_by_channel, sharing, enrolled, rng, as_of, allow_revoke):
    rows = []
    for channel, granted in granted_by_channel.items():
        revoked_on = None
        if granted and allow_revoke and rng.random() < 0.03:
            revoked_on = as_of - timedelta(days=rng.randint(20, 200))
        rows.append(
            Consent(
                patient_id=patient_id,
                purpose=ConsentPurpose.OUTREACH,
                channel=channel,
                granted=granted,
                effective_from=enrolled,
                effective_to=revoked_on,
            )
        )
        if revoked_on:
            rows.append(
                Consent(
                    patient_id=patient_id,
                    purpose=ConsentPurpose.OUTREACH,
                    channel=channel,
                    granted=False,
                    effective_from=revoked_on,
                    source="patient_request",
                )
            )
    rows.append(
        Consent(
            patient_id=patient_id,
            purpose=ConsentPurpose.PROVIDER_SHARING,
            channel=None,
            granted=sharing,
            effective_from=enrolled,
        )
    )
    return rows


def _hero_patient(cfg: GenConfig, i: int, hero: dict) -> PatientBundle:
    rng = _rng(cfg, "patient", i)
    state, city, zip3 = ref.GEOGRAPHY[0]
    pid = patient_key(i)
    therapies = []
    for measure, drug, ds, copay, delays, final_gap, prescriber, next_in in hero["therapies"]:
        if delays is None:
            start, fills = cfg.as_of - timedelta(days=final_gap), []
        else:
            fills = [cfg.as_of - timedelta(days=final_gap + ds)]
            for delay in reversed(delays):
                fills.insert(0, fills[0] - timedelta(days=ds + delay))
            start = fills[0]
        therapies.append(
            TherapySpec(
                measure,
                drug,
                _rxnorm(drug),
                ds,
                copay,
                prescriber,
                start,
                fixed_fills=fills,
                fixed_next_fill=cfg.as_of + timedelta(days=next_in) if next_in else None,
            )  # fmt: skip
        )
    enrolled = min(t.start for t in therapies) - timedelta(days=90)
    patient = Patient(
        patient_id=pid,
        first_name=hero["first_name"],
        last_name=hero["last_name"],
        birth_date=cfg.as_of - timedelta(days=int(hero["age"] * 365.25) + 100),
        sex=hero["sex"],
        city=city,
        state=state,
        zip=f"{zip3}{rng.randrange(100):02d}",
        plan_type=hero["plan_type"],
        preferred_channel=hero["traits"]["favourite_channel"],
    )
    consents = _consent_rows(
        pid, hero["consent"], hero["provider_sharing"], enrolled, rng, cfg.as_of, False
    )
    return PatientBundle(
        patient, hero["traits"], hero["primary_hcp"], therapies, consents, hero["history"]
    )


def _random_patient(cfg: GenConfig, i: int, hcps_by_state: dict) -> PatientBundle:
    rng = _rng(cfg, "patient", i)
    pid = patient_key(i)
    age = int(max(40, min(90, rng.gauss(66, 11))))
    sex = rng.choice("FM")
    state, city, zip3 = rng.choice(ref.GEOGRAPHY)
    if age >= 65:
        plan_weights = [("Medicare Advantage", 0.80), ("Commercial", 0.12), ("Medicaid", 0.08)]
    else:
        plan_weights = [("Medicare Advantage", 0.10), ("Commercial", 0.65), ("Medicaid", 0.25)]
    plan = behavior.weighted_choice(rng, plan_weights)
    traits = behavior.make_patient_traits(rng, age)

    local = hcps_by_state[state]
    primary = rng.choice(local["primary"])
    measures = [m for m in Measure if rng.random() < ref.MEASURE_PREVALENCE[m]]
    if not measures:
        measures = [behavior.weighted_choice(rng, ref.MEASURE_PREVALENCE.items())]

    therapies = []
    for m in measures:
        specialists = local[ref.SPECIALIST_FOR[m]]
        prescriber = rng.choice(specialists) if specialists and rng.random() < 0.28 else primary
        drug, rxnorm, brand = behavior.weighted_choice(
            rng, [(d, 0.35 if d[2] else 1.0) for d in ref.DRUGS[m]]
        )
        if plan == "Medicaid":
            copay = rng.uniform(3, 8) if brand else rng.uniform(0, 4)
        else:
            copay = rng.uniform(30, 60) if brand else rng.uniform(0, 15)
        therapies.append(
            TherapySpec(
                measure=m,
                drug=drug,
                rxnorm=rxnorm,
                days_supply=30 if rng.random() < 0.65 else 90,
                copay=round(copay, 2),
                prescriber=prescriber,
                start=cfg.as_of - timedelta(days=rng.randint(50, 540)),
            )
        )

    enrolled = min(t.start for t in therapies) - timedelta(days=rng.randint(30, 200))
    opted_out = rng.random() < ref.FULL_OPT_OUT_RATE
    granted = {
        ch: (not opted_out) and rng.random() < ref.CONSENT_RATE[ch] for ch in ref.PATIENT_CHANNELS
    }
    sharing = rng.random() < ref.PROVIDER_SHARING_RATE
    stated_pref = (
        traits["favourite_channel"] if rng.random() < 0.75 else rng.choice(ref.PATIENT_CHANNELS)
    )
    patient = Patient(
        patient_id=pid,
        first_name=rng.choice(ref.FIRST_NAMES_F if sex == "F" else ref.FIRST_NAMES_M),
        last_name=rng.choice(ref.LAST_NAMES),
        birth_date=cfg.as_of - timedelta(days=int(age * 365.25) + rng.randint(0, 364)),
        sex=sex,
        city=city,
        state=state,
        zip=f"{zip3}{rng.randrange(100):02d}",
        plan_type=plan,
        preferred_channel=stated_pref,
    )
    consents = _consent_rows(pid, granted, sharing, enrolled, rng, cfg.as_of, True)
    return PatientBundle(patient, traits, primary, therapies, consents)


def consent_checker(consents: list[Consent]):
    """Returns consented(channel, day) for outreach, from the patient's consent records."""

    def consented(channel: str, day: date) -> bool:
        return any(
            c.granted
            and c.purpose == ConsentPurpose.OUTREACH
            and c.channel == channel
            and c.effective_from <= day
            and (c.effective_to is None or day < c.effective_to)
            for c in consents
        )

    return consented


def simulate_therapy(
    rng: Random,
    as_of: date,
    traits: dict,
    patient_id: str,
    therapy_id: int,
    spec: TherapySpec,
    consented,
    touches: list[date],
    patient_content: list[Content],
) -> tuple[list[date], list[Interaction], date | None]:
    """Plays one therapy forward from its start to the as-of date.

    Returns fill dates, outreach interactions, and the date the patient would next fill
    without further outreach (None if they have silently stopped).
    """
    fills: list[date] = []
    interactions: list[Interaction] = []

    def touch(day: date, discontinued: bool) -> date | None:
        action = behavior.weighted_choice(rng, HISTORY_ACTIONS)
        channels = [Channel.PHONE] if action == ActionType.CHECK_IN else ref.PATIENT_CHANNELS
        allowed = [ch for ch in channels if consented(ch, day)]
        if not allowed:
            return None
        channel = rng.choice(allowed)
        recent = sum(1 for d in touches if 0 <= (day - d).days <= 14)
        touches.append(day)
        ts = _at(day, rng)
        matches = [
            c
            for c in patient_content
            if c.action_type == action
            and channel in c.channels
            and c.measure in (spec.measure, None)
            and valid_on(c, day)
        ]
        fill_day, outcome, outcome_ts = None, Outcome.NO_RESPONSE, None
        if rng.random() < behavior.patient_engage_prob(traits, channel, recent):
            if rng.random() < behavior.patient_fill_prob(traits, action, discontinued):
                fill_day = day + timedelta(days=rng.randint(1, 4))
            if fill_day and fill_day <= as_of:
                outcome, outcome_ts = Outcome.FILLED, _past(datetime.combine(fill_day, time(12, 0)))
            else:
                outcome = (
                    Outcome.COMPLETED
                    if channel == Channel.PHONE
                    else behavior.weighted_choice(rng, DIGITAL_OUTCOMES)
                )
                outcome_ts = _past(ts + timedelta(hours=rng.randint(0, 6)))
        interactions.append(
            Interaction(
                target_type=TargetType.PATIENT,
                target_id=patient_id,
                channel=channel,
                int_ts=ts,
                type=action,
                outcome=outcome,
                outcome_ts=outcome_ts,
                content_id=matches[0].content_id if matches else None,
                therapy_id=therapy_id,
            )
        )
        return fill_day

    def next_fill_after(runout: date, natural_next: date | None) -> date | None:
        """Runs the outreach that happens while waiting for a refill."""
        day = runout + timedelta(days=rng.randint(-3, 12))
        p_send = 0.6
        while day <= as_of and (natural_next is None or day < natural_next):
            if rng.random() < p_send:
                triggered = touch(day, discontinued=natural_next is None)
                if triggered:
                    return triggered
            day += timedelta(days=rng.randint(21, 35))
            p_send = 0.45
        return natural_next

    fill_day: date | None = spec.start
    if traits["archetype"] == "never_starter" and rng.random() < 0.7:
        fill_day = next_fill_after(spec.start, None)

    k = 0
    while fill_day is not None and fill_day <= as_of:
        fills.append(fill_day)
        runout = fill_day + timedelta(days=spec.days_supply)
        if rng.random() < behavior.discontinue_hazard(traits, k, spec.copay):
            natural_next = None
        else:
            delay = behavior.natural_refill_delay(rng, traits, k, spec.copay)
            natural_next = runout + timedelta(days=delay)
        fill_day = next_fill_after(runout, natural_next)
        k += 1
    return fills, interactions, fill_day


# --- Users and assignments ---------------------------------------------------


def build_users(cfg: GenConfig, hcps: list[Hcp], patients: list[Patient]):
    """Demo accounts for the seeded personas: real accounts with an email and a password."""
    pw = hash_password(cfg.demo_password or get_settings().secret("demo_password"))
    domain = get_settings().demo_email_domain

    def user(username, name, role, **kw):
        # Seeded staff were provisioned by the platform, not invited: they carry the
        # professional verification with source "system".
        professional = role in PROFESSIONAL_ROLES
        return User(
            username=username,
            email=f"{username}@{domain}",
            display_name=name,
            password_hash=pw,
            role=role,
            verified=True,
            status=AccountStatus.ACTIVE,
            source=AccountSource.SEED,
            professionally_verified=professional,
            professionally_verified_at=utcnow() if professional else None,
            verification_source=VerificationSource.SYSTEM if professional else None,
            **kw,
        )

    staff = [
        user("compliance1", "Priya Nair (MLR Reviewer)", Role.COMPLIANCE),
        user("compliance2", "Jon Becker (Privacy)", Role.COMPLIANCE),
    ]
    reps = [
        user(f"rep{n:02d}", f"Medical Rep {n:02d}", Role.MEDICAL_REP)
        for n in range(1, cfg.n_reps + 1)
    ]
    cms = [
        user(f"cm{n:02d}", f"Care Manager {n:02d}", Role.CARE_MANAGER)
        for n in range(1, cfg.n_care_managers + 1)
    ]
    portal = [
        user(
            h.hcp_id.lower().replace("_", ""),
            f"Dr. {h.first_name} {h.last_name}",
            Role.HCP,
            hcp_id=h.hcp_id,
        )
        for h in hcps[: len(HCP_HEROES)]
    ] + [
        user(
            p.patient_id.lower().replace("_", ""),
            f"{p.first_name} {p.last_name}",
            Role.PATIENT,
            patient_id=p.patient_id,
            date_of_birth=p.birth_date,
        )
        for p in patients[: len(PATIENT_HEROES)]
    ]
    return staff, reps, cms, portal


def assign_reps(reps: list[User], hcps: list[Hcp]) -> list[RepHcp]:
    """One territory per state, shared round-robin by the reps covering it. Heroes go to rep 1."""
    states = sorted({h.state for h in hcps})
    reps_in_state = defaultdict(list)
    for n, rep in enumerate(reps):
        reps_in_state[states[n % len(states)]].append(rep)
    rows, counter = [], defaultdict(int)
    for n, hcp in enumerate(hcps, start=1):
        if n in HCP_HEROES:
            rep = reps[0]
        else:
            pool = reps_in_state[hcp.state] or reps
            rep = pool[counter[hcp.state] % len(pool)]
            counter[hcp.state] += 1
        rows.append(RepHcp(rep_user_id=rep.id, hcp_id=hcp.hcp_id))
    return rows


def assign_care_managers(cms: list[User], patients: list[Patient]) -> list[CareManagerPatient]:
    return [
        CareManagerPatient(
            care_manager_user_id=(cms[0] if n in PATIENT_HEROES else cms[n % len(cms)]).id,
            patient_id=p.patient_id,
        )
        for n, p in enumerate(patients, start=1)
    ]


# --- Orchestration -----------------------------------------------------------


def _hcps_by_state(hcps: list[Hcp]) -> dict:
    hcps_by_state: dict = defaultdict(lambda: defaultdict(list))
    for h in hcps:
        key = "primary" if h.specialty in ref.PRIMARY_CARE else h.specialty
        hcps_by_state[h.state][key].append(h.hcp_id)
    all_primary = [h.hcp_id for h in hcps if h.specialty in ref.PRIMARY_CARE]
    for state_map in hcps_by_state.values():
        state_map["primary"] = state_map["primary"] or all_primary
    for state, _, _ in ref.GEOGRAPHY:
        hcps_by_state[state]["primary"] = hcps_by_state[state]["primary"] or all_primary
    return hcps_by_state


def synthetic_hcp_names(hcp_ids: list[str]) -> dict[str, tuple[str, str]]:
    """The generated (first, last) name of synthetic HCP records, recomputed from the seed."""
    wanted = {i for i in hcp_ids if i.startswith("HCP_") and i[4:].isdigit()}
    if not wanted:
        return {}
    top = max(int(i[4:]) for i in wanted)
    cfg = GenConfig(n_hcps=max(top, 12), seed_demo_accounts=False)
    return {h.hcp_id: (h.first_name, h.last_name) for h in build_hcps(cfg)[0] if h.hcp_id in wanted}


def synthetic_patient_names(n_hcps: int, patient_ids: list[str]) -> dict[str, tuple[str, str]]:
    """The generated (first, last) name of synthetic patient records, recomputed from the
    seed. Used to give a record its own name back when an account stops using it."""
    cfg = GenConfig(n_hcps=n_hcps, seed_demo_accounts=False)
    hcps_by_state = _hcps_by_state(build_hcps(cfg)[0])
    names = {}
    for pid in patient_ids:
        if not pid.startswith("PAT_") or not pid[4:].isdigit():
            continue
        i = int(pid[4:])
        bundle = (
            _hero_patient(cfg, i, PATIENT_HEROES[i])
            if i in PATIENT_HEROES
            else _random_patient(cfg, i, hcps_by_state)
        )
        names[pid] = (bundle.patient.first_name, bundle.patient.last_name)
    return names


def generate(db: Session, cfg: GenConfig | None = None) -> dict[str, int]:
    """Replaces all data with a freshly generated synthetic population."""
    cfg = cfg or GenConfig()
    if cfg.n_hcps < 12 or cfg.n_patients < len(PATIENT_HEROES):
        raise ValueError("scale too small: need at least 12 HCPs and 6 patients")
    # Registered and invited accounts, invitations and the security audit trail are not
    # demo data: carry them across the rebuild. No session survives it.
    registered = assignments.snapshot(db)
    # Real patients (self-registered or clinic) and what they and their care team recorded
    # are not demo data either.
    registered["real_patients"] = records.snapshot(db)
    registered["real_hcps"] = hcps_module.snapshot(db)
    wipe(db)
    sessions.revoke_everyone(db)
    ensure_system_admin(db)

    contents = build_content(cfg.as_of)
    hcps, hcp_traits = build_hcps(cfg)
    db.add_all(contents + hcps)
    db.flush()
    db.add_all(HcpSpecialty(hcp_id=h.hcp_id, specialty=h.specialty) for h in hcps)

    hcps_by_state = _hcps_by_state(hcps)
    bundles = [
        _hero_patient(cfg, i, PATIENT_HEROES[i])
        if i in PATIENT_HEROES
        else _random_patient(cfg, i, hcps_by_state)
        for i in range(1, cfg.n_patients + 1)
    ]
    patients = [b.patient for b in bundles]
    db.add_all(patients)
    db.flush()

    therapy_rows: list[tuple[PatientBundle, TherapySpec, PatientTherapy]] = []
    for b in bundles:
        attributed = {b.primary_hcp} | {t.prescriber for t in b.therapies}
        db.add_all(
            PatientHcp(patient_id=b.patient.patient_id, hcp_id=h, is_primary=h == b.primary_hcp)
            for h in sorted(attributed)
        )
        db.add_all(b.consents)
        for spec in b.therapies:
            row = PatientTherapy(
                patient_id=b.patient.patient_id,
                measure=spec.measure,
                drug_name=spec.drug,
                rxnorm=spec.rxnorm,
                prescriber_hcp_id=spec.prescriber,
                start_date=spec.start,
                days_supply=spec.days_supply,
                copay=spec.copay,
            )
            db.add(row)
            therapy_rows.append((b, spec, row))
    db.flush()

    patient_content = [c for c in contents if c.audience == TargetType.PATIENT]
    touches_by_patient: dict[str, list[date]] = defaultdict(list)
    latent_therapies: dict[str, dict] = defaultdict(dict)
    interactions: list[Interaction] = []
    for b, spec, row in therapy_rows:
        pid = b.patient.patient_id
        if spec.fixed_fills is not None:
            fills, next_fill = spec.fixed_fills, spec.fixed_next_fill
        else:
            fills, history, next_fill = simulate_therapy(
                _rng(cfg, "therapy", pid, spec.measure),
                cfg.as_of,
                b.traits,
                pid,
                row.id,
                spec,
                consent_checker(b.consents),
                touches_by_patient[pid],
                patient_content,
            )
            interactions.extend(history)
        db.add_all(
            MedicationFill(
                patient_id=pid,
                therapy_id=row.id,
                drug_name=spec.drug,
                fill_date=day,
                days_supply=spec.days_supply,
                quantity=spec.days_supply,
                copay=spec.copay,
            )
            for day in fills
        )
        latent_therapies[pid][str(row.id)] = {
            "next_fill": next_fill.isoformat() if next_fill else None,
            "days_supply": spec.days_supply,
            "copay": spec.copay,
        }

    first_therapy: dict[str, int] = {}
    for b, _, row in therapy_rows:
        first_therapy.setdefault(b.patient.patient_id, row.id)
    for i, b in enumerate(bundles, start=1):
        rng = _rng(cfg, "patient_history", i)
        for days_ago, channel, action, outcome in b.fixed_history or []:
            ts = _at(cfg.as_of - timedelta(days=days_ago), rng)
            engaged = outcome != Outcome.NO_RESPONSE
            interactions.append(
                Interaction(
                    target_type=TargetType.PATIENT,
                    target_id=b.patient.patient_id,
                    channel=channel,
                    int_ts=ts,
                    type=action,
                    outcome=outcome,
                    outcome_ts=_past(ts + timedelta(hours=2)) if engaged else None,
                    therapy_id=first_therapy[b.patient.patient_id],
                )
            )

    interactions.extend(build_hcp_history(cfg, hcps, hcp_traits, contents))
    interactions.sort(key=lambda x: x.int_ts)
    db.add_all(interactions)

    db.add_all(
        SimLatent(target_type=TargetType.HCP, target_id=hcp_id, traits=t)
        for hcp_id, t in hcp_traits.items()
    )
    db.add_all(
        SimLatent(
            target_type=TargetType.PATIENT,
            target_id=b.patient.patient_id,
            traits={**b.traits, "therapies": latent_therapies[b.patient.patient_id]},
        )
        for b in bundles
    )

    if cfg.seed_demo_accounts:
        staff, reps, cms, portal = build_users(cfg, hcps, patients)
        db.add_all(staff + reps + cms + portal)
        db.flush()
        db.add_all(assign_reps(reps, hcps))
        db.add_all(assign_care_managers(cms, patients))
        db.flush()
    db.flush()
    # Synthetic people's activity comes from their generated history, not the generation time.
    activity.backfill(db, only_missing=False, synthetic_only=True)
    hcps_module.restore(db, registered.get("real_hcps", {}))
    records.restore(db, registered.get("real_patients", {}))
    assignments.restore(db, registered)
    records.restore_links(db, registered.get("real_patients", {}))
    hcps_module.restore_links(db, registered.get("real_hcps", {}))

    # The outcome simulator has played the population up to its generation day.
    clock.set_simulated_through(db, cfg.as_of)
    db.commit()
    return {
        table.name: db.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    }
