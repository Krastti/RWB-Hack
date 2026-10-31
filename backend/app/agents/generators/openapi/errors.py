"""Stable diagnostics shared by policy validation and the compiler."""


class OpenAPIError(ValueError):
    """Only a fixed public diagnostic code, never a provider value or exception."""


def require(condition, code):
    if not condition:
        raise OpenAPIError(code)
