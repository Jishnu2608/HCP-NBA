from fastapi import HTTPException


class AuthError(HTTPException):
    """An authentication failure with a stable machine-readable code for the client."""

    def __init__(self, status: int, code: str, message: str, **extra) -> None:
        super().__init__(status, {"code": code, "message": message, **extra})
        self.code = code


def invalid_credentials() -> AuthError:
    # Deliberately the same for an unknown email and a wrong password.
    return AuthError(401, "invalid_credentials", "Incorrect email or password.")
