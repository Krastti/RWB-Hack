"""Project service errors for platform contract C-01."""


class InvalidProjectInput(ValueError):
    """Invalid fields or pagination cursor; HTTP adapters map this to 422."""


class ProjectNotFound(LookupError):
    """Missing and foreign projects share this error; map to HTTP 404."""
