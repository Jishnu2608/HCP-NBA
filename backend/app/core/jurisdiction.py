"""Where an account holder lives, and what follows from it.

Collected at sign-up and invitation acceptance as a country (ISO 3166-1 alpha-2) and, for
the United States, a state. Nothing finer: no address, no postcode.

The age of adulthood is not the same everywhere. `adult_age()` returns the configured
default (`NBA_MINOR_AGE`, 18) unless the table below overrides it. The overrides are
starting values that must be confirmed by counsel before production, and new
jurisdiction-specific rules (for example guardian consent) belong here, not in callers.
"""

from app.auth.errors import AuthError
from app.core.config import get_settings

# The markets the product is designed for (United States, EU/EEA, UK, Switzerland), and
# "Other" for anyone else.
COUNTRIES: dict[str, str] = {
    "US": "United States",
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France",
    "DE": "Germany", "GR": "Greece", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
    "LV": "Latvia", "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta",
    "NL": "Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "SK": "Slovakia", "SI": "Slovenia", "ES": "Spain", "SE": "Sweden",
    "IS": "Iceland", "LI": "Liechtenstein", "NO": "Norway",
    "GB": "United Kingdom", "CH": "Switzerland",
    "ZZ": "Other country",
}  # fmt: skip

EU_EEA = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "IS", "LI", "NO",
}  # fmt: skip

US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}  # fmt: skip

# Age of adulthood where it differs from the default. TO BE CONFIRMED BY COUNSEL.
ADULT_AGE_OVERRIDES: dict[str, int] = {"US-AL": 19, "US-NE": 19, "US-MS": 21}


def code(country: str | None, region: str | None) -> str | None:
    if not country:
        return None
    return f"{country}-{region}" if region else country


def validate(country: str, region: str | None) -> tuple[str, str | None]:
    """A known country, and a known state exactly when the country is the United States."""
    country = (country or "").strip().upper()
    region = (region or "").strip().upper() or None
    if country not in COUNTRIES:
        raise AuthError(422, "invalid_country", "Choose your country of residence.")
    if country == "US":
        if region not in US_STATES:
            raise AuthError(422, "invalid_region", "Choose your state.")
    elif region is not None:
        raise AuthError(422, "invalid_region", "A state applies only to the United States.")
    return country, region


def adult_age(country: str | None, region: str | None) -> int:
    key = code(country, region)
    return ADULT_AGE_OVERRIDES.get(key or "", get_settings().minor_age)


def is_eu_eea(country: str | None) -> bool:
    return country in EU_EEA


def options() -> dict:
    """For the sign-up and invitation forms."""
    return {
        "countries": [{"code": c, "name": n} for c, n in COUNTRIES.items()],
        "us_states": [{"code": c, "name": n} for c, n in US_STATES.items()],
    }
