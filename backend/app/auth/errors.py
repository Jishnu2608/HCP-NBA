from fastapi import HTTPException


class AuthError(HTTPException):
    """An authentication failure with a stable machine-readable code for the client.

    `verify_token` is never part of the response body: when set, the API layer puts it in
    the HttpOnly verification cookie (for example a sign-in that still needs the code).
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        verify_token: str | None = None,
        **extra,
    ) -> None:
        super().__init__(status, {"code": code, "message": message, **extra}, headers=headers)
        self.code = code
        self.verify_token = verify_token


def invalid_credentials() -> AuthError:
    # Deliberately the same for an unknown email and a wrong password.
    return AuthError(401, "invalid_credentials", "Incorrect email or password.")
