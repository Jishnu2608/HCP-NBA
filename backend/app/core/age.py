"""Date of birth, age and minor status. The one place these are worked out.

The browser never sends an age or a minor flag; both are derived here from the date of
birth stored on the account. "Today" is the real calendar date, not the demo clock: an
account holder's age is a fact about the person, not about the simulation.
"""

from datetime import UTC, date, datetime

from app.auth.errors import AuthError
from app.core.config import get_settings
from app.models import User

OLDEST = 120


def today() -> date:
    return datetime.now(UTC).date()


def age_on(dob: date, day: date) -> int:
    """Whole years completed on `day`."""
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


def parse_dob(value: str | date) -> date:
    """A valid, plausible date of birth, or a 422 the form can show next to the field."""
    if isinstance(value, date):
        dob = value
    else:
        try:
            dob = date.fromisoformat(str(value).strip())
        except ValueError:
            raise AuthError(422, "invalid_dob", "Enter a valid date of birth.") from None
    now = today()
    if dob > now:
        raise AuthError(422, "invalid_dob", "Date of birth cannot be in the future.")
    if age_on(dob, now) > OLDEST:
        raise AuthError(422, "invalid_dob", "Enter a valid date of birth.")
    return dob


def is_minor_dob(dob: date | None) -> bool | None:
    """True / False, or None when no date of birth is on file (system and seeded staff)."""
    if dob is None:
        return None
    return age_on(dob, today()) < get_settings().minor_age


def is_minor(user: User) -> bool | None:
    return is_minor_dob(user.date_of_birth)


def age_band(user: User) -> str:
    """What an account manager may see instead of the date of birth."""
    minor = is_minor(user)
    return "unknown" if minor is None else ("minor" if minor else "adult")
