"""Authentication errors shared by service and API adapters."""


class Unauthorized(Exception):
    """Uniform failure for credentials, tokens and inactive/missing users."""

    def __init__(self) -> None:
        super().__init__("Authentication required")
