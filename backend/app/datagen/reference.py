"""Static reference data for the synthetic generator.

Specialty taxonomy codes and drug names/RxNorm ingredient codes are real public vocabulary.
Everything about individual people is invented.
"""

from app.clinical.vocabulary import DRUGS, PRIMARY_CARE, SPECIALIST_FOR  # noqa: F401
from app.models.enums import Measure

# (specialty, NUCC taxonomy code, share of HCPs, median annual Rx volume)
SPECIALTIES = [
    ("Family Medicine", "207Q00000X", 0.40, 2600),
    ("Internal Medicine", "207R00000X", 0.30, 3000),
    ("Cardiovascular Disease", "207RC0000X", 0.17, 2200),
    ("Endocrinology", "207RE0101X", 0.13, 1900),
]
MEASURE_PREVALENCE = {
    Measure.DIABETES: 0.35,
    Measure.HYPERTENSION: 0.60,
    Measure.CHOLESTEROL: 0.50,
}

PLAN_TYPES = [("Medicare Advantage", 0.55), ("Commercial", 0.30), ("Medicaid", 0.15)]

# (state, city, zip prefix). One rep territory per state.
GEOGRAPHY = [
    ("MA", "Boston", "021"),
    ("TX", "Houston", "770"),
    ("FL", "Tampa", "336"),
    ("OH", "Columbus", "432"),
    ("AZ", "Phoenix", "850"),
    ("PA", "Pittsburgh", "152"),
]

ORGANIZATIONS = [
    "{city} Community Health Partners",
    "{city} Medical Group",
    "Riverside Physicians of {city}",
    "{city} Heart and Metabolic Clinic",
    "Lakeview Primary Care {city}",
]

FIRST_NAMES_F = [
    "Mary", "Patricia", "Linda", "Barbara", "Elizabeth", "Susan", "Margaret", "Dorothy",
    "Nancy", "Karen", "Helen", "Sandra", "Donna", "Carol", "Ruth", "Sharon", "Maria",
    "Angela", "Priya", "Mei", "Fatima", "Rosa", "Aisha", "Yuki", "Elena",
]  # fmt: skip
FIRST_NAMES_M = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard", "Charles",
    "Joseph", "Thomas", "Daniel", "Paul", "Mark", "George", "Kenneth", "Steven", "Carlos",
    "Luis", "Raj", "Wei", "Omar", "Andre", "Hiro", "Ivan", "Samuel",
]  # fmt: skip
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Wilson", "Anderson", "Thomas", "Taylor",
    "Moore", "Jackson", "Martin", "Lee", "Thompson", "White", "Harris", "Clark", "Lewis",
    "Robinson", "Walker", "Young", "Allen", "King", "Wright", "Scott", "Nguyen", "Hill",
    "Flores", "Green", "Adams", "Nelson", "Baker", "Patel", "Kim", "Chen", "Shah", "Khan",
    "Rivera", "Campbell", "Mitchell", "Carter", "Roberts", "Okafor",
]  # fmt: skip

PATIENT_CHANNELS = ("sms", "email", "portal", "phone")
HCP_CHANNELS = ("email", "portal", "rep_visit", "phone")
# Share of patients granting outreach consent per channel (among those not fully opted out).
CONSENT_RATE = {"sms": 0.70, "email": 0.82, "portal": 0.62, "phone": 0.76}
FULL_OPT_OUT_RATE = 0.05
PROVIDER_SHARING_RATE = 0.80

SUBTOPICS = ("outcomes", "guidelines", "adherence", "program")
