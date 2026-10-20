class NotFoundError(LookupError):
    """Raised by domain code when an id does not exist. The API maps only this class to 404."""


class InvalidInputError(ValueError):
    """Raised for invalid user input. The API maps it to 400 with `code` as the error code."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
