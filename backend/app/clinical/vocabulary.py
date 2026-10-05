"""The single source of the conditions, medications and specialties the product knows about.

Conditions map to the three adherence measures the engine supports, plus "other" with the
person's own words. Each condition lists the specialties a care manager may route the
patient to: the specialist first, then primary care. Nothing here diagnoses anyone; a
condition exists only because the patient or their care team recorded it.
"""

from app.models.enums import Measure

PRIMARY_CARE = ("Family Medicine", "Internal Medicine")

# The controlled list of HCP specialties (code -> display label). Codes are the NUCC
# specialty names the synthetic data uses; an HCP may hold several. No default exists.
SPECIALTIES: dict[str, str] = {
    "Cardiovascular Disease": "Cardiology",
    "Endocrinology": "Endocrinology",
    "Family Medicine": "Family Medicine",
    "Internal Medicine": "Internal Medicine (general medicine)",
}


def specialty_label(code: str) -> str:
    return SPECIALTIES.get(code, code)


def specialty_options() -> list[dict]:
    return [{"code": code, "label": label} for code, label in SPECIALTIES.items()]


# Specialist per adherence measure (also used by the synthetic data generator).
SPECIALIST_FOR: dict[str, str] = {
    Measure.DIABETES: "Endocrinology",
    Measure.HYPERTENSION: "Cardiovascular Disease",
    Measure.CHOLESTEROL: "Cardiovascular Disease",
}

OTHER = "other"

# code -> (label, adherence measure or None)
CONDITIONS: dict[str, tuple[str, str | None]] = {
    "type2_diabetes": ("Type 2 diabetes", Measure.DIABETES),
    "hypertension": ("High blood pressure (hypertension)", Measure.HYPERTENSION),
    "high_cholesterol": ("High cholesterol", Measure.CHOLESTEROL),
    OTHER: ("Other condition", None),
}

# measure -> [(generic name, RxNorm ingredient code, is_brand_tier)]
DRUGS: dict[str, list[tuple[str, str, bool]]] = {
    Measure.DIABETES: [
        ("metformin", "6809", False),
        ("glipizide", "4821", False),
        ("sitagliptin", "593411", True),
        ("empagliflozin", "1545653", True),
    ],
    Measure.HYPERTENSION: [
        ("lisinopril", "29046", False),
        ("losartan", "52175", False),
        ("valsartan", "69749", False),
        ("ramipril", "35296", False),
    ],
    Measure.CHOLESTEROL: [
        ("atorvastatin", "83367", False),
        ("rosuvastatin", "301542", False),
        ("simvastatin", "36567", False),
        ("pravastatin", "42463", False),
    ],
}

DAYS_SUPPLY_CHOICES = (30, 60, 90)


def condition_label(code: str, other_text: str | None = None) -> str:
    label = CONDITIONS.get(code, (code, None))[0]
    return other_text or label if code == OTHER else label


def measure_of_condition(code: str) -> str | None:
    return CONDITIONS.get(code, (None, None))[1]


def specialties_for(code: str) -> list[str]:
    """Specialties suited to a condition, specialist first. Primary care for anything else."""
    measure = measure_of_condition(code)
    specialist = SPECIALIST_FOR.get(measure) if measure else None
    return ([specialist] if specialist else []) + list(PRIMARY_CARE)


def drug_info(name: str) -> tuple[str | None, str | None]:
    """(measure, RxNorm code) for a known generic name; (None, None) otherwise."""
    key = name.strip().lower()
    for measure, drugs in DRUGS.items():
        for drug, rxnorm, _ in drugs:
            if drug == key:
                return measure, rxnorm
    return None, None


def medication_options() -> list[dict]:
    return [
        {"name": drug, "measure": str(measure)}
        for measure, drugs in DRUGS.items()
        for drug, _, _ in drugs
    ]


def condition_options() -> list[dict]:
    return [
        {"code": code, "label": label, "specialties": specialties_for(code)}
        for code, (label, _) in CONDITIONS.items()
    ]
