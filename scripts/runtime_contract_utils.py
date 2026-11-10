"""Integrity checks for the runtime contract runner."""


def require(condition: object, message: object) -> None:
    if not condition:
        raise RuntimeError(message)
