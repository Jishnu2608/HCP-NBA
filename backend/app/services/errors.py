"""Domain errors raised by the read services. Routers translate them to HTTP answers."""


class ReadError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(ReadError):
    """The record does not exist or is outside the reader's scope (indistinguishable)."""


class Forbidden(ReadError):
    """The reader's role has no permission for this kind of record at all."""


class NoRecord(ReadError):
    """A self-service reader whose account is not linked to a patient or HCP record."""
