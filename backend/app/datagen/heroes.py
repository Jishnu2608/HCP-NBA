"""Hand-pinned records that guarantee the demo scenarios exist for any seed and any scale.

Keys are the 1-based index of the HCP or patient, so HCP 1 is HCP_0001 and patient 1 is PAT_00001.
"""

HCP_HEROES = {
    # Scenario 1: high-value cardiologist, responds to email, approved content available.
    1: {
        "first_name": "Elena",
        "last_name": "Marsh",
        "specialty": "Cardiovascular Disease",
        "rx_volume": 5200,
        "traits": {
            "favourite_channel": "email",
            "channel_resp": {"email": 0.80, "portal": 0.35, "rep_visit": 0.15, "phone": 0.05},
            "measure_interest": {"diabetes": 0.30, "hypertension": 0.90, "cholesterol": 0.90},
            "subtopic_pref": {"outcomes": 1.0, "guidelines": 0.7, "adherence": 0.6, "program": 0.4},
            "digital_fatigue": 0.20,
        },
        # (days before as-of, channel, topic, action, outcome). Replaces random history so the
        # pattern is unambiguous: opens email, declines rep visits, has not yet seen outcomes data.
        "replace_history": True,
        "history": [
            (300, "email", "hypertension_guidelines", "share_study", "clicked"),
            (250, "email", "cholesterol_guidelines", "share_study", "opened"),
            (200, "email", "hypertension_adherence", "hcp_education", "clicked"),
            (160, "rep_visit", "hypertension_adherence", "rep_visit", "declined"),
            (120, "email", "general_adherence", "hcp_education", "opened"),
            (90, "rep_visit", "cholesterol_adherence", "rep_visit", "declined"),
            (60, "email", "diabetes_guidelines", "share_study", "no_response"),
            (35, "email", "cholesterol_adherence", "hcp_education", "clicked"),
        ],
    },
    # Scenario 2: endocrinologist whose best-matching content (diabetes outcomes) is not cleared.
    2: {
        "first_name": "Rajan",
        "last_name": "Iyer",
        "specialty": "Endocrinology",
        "rx_volume": 4100,
        "traits": {
            "favourite_channel": "portal",
            "channel_resp": {"email": 0.45, "portal": 0.75, "rep_visit": 0.20, "phone": 0.05},
            "measure_interest": {"diabetes": 0.95, "hypertension": 0.30, "cholesterol": 0.40},
            "subtopic_pref": {
                "outcomes": 1.0,
                "guidelines": 0.5,
                "adherence": 0.45,
                "program": 0.3,
            },
            "digital_fatigue": 0.25,
        },
        # Added to the random history: engagement with the now-expired outcomes summary.
        "history": [
            (190, "portal", "diabetes_outcomes", "share_study", "clicked"),
            (120, "portal", "diabetes_outcomes", "share_study", "clicked"),
            (75, "email", "diabetes_outcomes", "share_study", "opened"),
        ],
    },
    # Contrast: low-value, digitally fatigued primary-care physician.
    3: {
        "first_name": "Thomas",
        "last_name": "Okafor",
        "specialty": "Family Medicine",
        "rx_volume": 900,
        "traits": {
            "favourite_channel": "rep_visit",
            "channel_resp": {"email": 0.08, "portal": 0.05, "rep_visit": 0.30, "phone": 0.05},
            "measure_interest": {"diabetes": 0.5, "hypertension": 0.5, "cholesterol": 0.5},
            "subtopic_pref": {"outcomes": 0.5, "guidelines": 0.6, "adherence": 0.5, "program": 0.5},
            "digital_fatigue": 0.90,
        },
    },
}

_ALL = {"sms": True, "email": True, "portal": True, "phone": True}
_NONE = {"sms": False, "email": False, "portal": False, "phone": False}


def _traits(archetype, favourite, channel_resp, action_effect):
    return {
        "archetype": archetype,
        "favourite_channel": favourite,
        "channel_resp": channel_resp,
        "action_effect": action_effect,
    }


# Therapy tuple: (measure, drug, days_supply, copay, delays, final_gap, prescriber, next_fill_in)
#   delays         days late for each refill after the first; None = prescription never filled
#   final_gap      days since the last supply ran out at the as-of date (negative = still covered)
#   next_fill_in   days after as-of when the patient would refill unprompted; None = never
# History tuple: (days before as-of, channel, action, outcome)
PATIENT_HEROES = {
    # Scenario 3: forgetful, consented, widening gap, responds to text.
    1: {
        "first_name": "Margaret",
        "last_name": "Doyle",
        "age": 67,
        "sex": "F",
        "plan_type": "Medicare Advantage",
        "primary_hcp": "HCP_0003",
        "therapies": [
            ("diabetes", "metformin", 30, 5.0, [0, 2, 3, 5, 8, 12], 16, "HCP_0002", 20),
        ],
        "consent": _ALL,
        "provider_sharing": True,
        "traits": _traits(
            "forgetful",
            "sms",
            {"sms": 0.85, "email": 0.30, "portal": 0.20, "phone": 0.45},
            {"refill_nudge": 0.85, "check_in": 0.60, "education": 0.20, "cost_support": 0.10},
        ),
        "history": [
            (150, "sms", "refill_nudge", "clicked"),
            (95, "sms", "refill_nudge", "replied"),
            (60, "email", "education", "no_response"),
        ],
    },
    # Scenario 4: prefers text but has not consented to it; other channels allowed.
    2: {
        "first_name": "Daniel",
        "last_name": "Reyes",
        "age": 58,
        "sex": "M",
        "plan_type": "Commercial",
        "primary_hcp": "HCP_0003",
        "therapies": [
            ("hypertension", "lisinopril", 30, 4.0, [1, 4, 6, 10], 21, "HCP_0001", 25),
            ("cholesterol", "atorvastatin", 90, 8.0, [2], -40, "HCP_0001", 43),
        ],
        "consent": {"sms": False, "email": True, "portal": True, "phone": True},
        "provider_sharing": False,
        "traits": _traits(
            "forgetful",
            "sms",
            {"sms": 0.80, "email": 0.40, "portal": 0.30, "phone": 0.35},
            {"refill_nudge": 0.75, "check_in": 0.55, "education": 0.20, "cost_support": 0.10},
        ),
        "history": [
            (110, "email", "refill_nudge", "opened"),
            (70, "phone", "check_in", "completed"),
        ],
    },
    # Contrast: steady, adherent patient. Should not be nudged.
    3: {
        "first_name": "Helen",
        "last_name": "Baker",
        "age": 71,
        "sex": "F",
        "plan_type": "Medicare Advantage",
        "primary_hcp": "HCP_0003",
        "therapies": [
            (
                "hypertension",
                "losartan",
                30,
                3.0,
                [0, 0, -1, 0, 1, 0, 0, -1, 0, 0],
                -12,
                "HCP_0003",
                12,
            ),
        ],
        "consent": _ALL,
        "provider_sharing": True,
        "traits": _traits(
            "steady",
            "portal",
            {"sms": 0.30, "email": 0.35, "portal": 0.55, "phone": 0.40},
            {"refill_nudge": 0.55, "check_in": 0.50, "education": 0.40, "cost_support": 0.30},
        ),
        "history": [(130, "portal", "education", "opened")],
    },
    # High risk but opted out of all outreach: every recommendation must be blocked.
    4: {
        "first_name": "George",
        "last_name": "Whitman",
        "age": 74,
        "sex": "M",
        "plan_type": "Medicare Advantage",
        "primary_hcp": "HCP_0003",
        "therapies": [
            ("hypertension", "ramipril", 30, 6.0, [3, 7, 12, 18], 35, "HCP_0001", None),
        ],
        "consent": _NONE,
        "provider_sharing": True,
        "traits": _traits(
            "drifter",
            "phone",
            {"sms": 0.10, "email": 0.15, "portal": 0.08, "phone": 0.50},
            {"refill_nudge": 0.20, "check_in": 0.55, "education": 0.45, "cost_support": 0.10},
        ),
        "history": [],
    },
    # Cost barrier on a brand-tier drug: reminders fail, cost support works.
    5: {
        "first_name": "Rosa",
        "last_name": "Delgado",
        "age": 62,
        "sex": "F",
        "plan_type": "Commercial",
        "primary_hcp": "HCP_0003",
        "therapies": [
            ("diabetes", "empagliflozin", 30, 55.0, [4, 9, 15], 24, "HCP_0002", 30),
        ],
        "consent": _ALL,
        "provider_sharing": True,
        "traits": _traits(
            "cost_barrier",
            "phone",
            {"sms": 0.45, "email": 0.35, "portal": 0.25, "phone": 0.65},
            {"refill_nudge": 0.06, "check_in": 0.30, "education": 0.10, "cost_support": 0.80},
        ),
        "history": [
            (100, "sms", "refill_nudge", "no_response"),
            (66, "sms", "refill_nudge", "opened"),
            (40, "sms", "refill_nudge", "no_response"),
        ],
    },
    # Primary non-adherence: new prescription never filled.
    6: {
        "first_name": "Samuel",
        "last_name": "Nguyen",
        "age": 55,
        "sex": "M",
        "plan_type": "Commercial",
        "primary_hcp": "HCP_0003",
        "therapies": [
            ("cholesterol", "atorvastatin", 30, 6.0, None, 20, "HCP_0001", None),
        ],
        "consent": _ALL,
        "provider_sharing": True,
        "traits": _traits(
            "never_starter",
            "email",
            {"sms": 0.40, "email": 0.60, "portal": 0.35, "phone": 0.30},
            {"refill_nudge": 0.10, "check_in": 0.40, "education": 0.55, "cost_support": 0.30},
        ),
        "history": [],
    },
}
