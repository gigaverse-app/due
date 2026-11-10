"""Shared integrity checks for the source verifier bootstrap and runner."""


def require(condition: object, message: object) -> None:
    if not condition:
        raise RuntimeError(message)
