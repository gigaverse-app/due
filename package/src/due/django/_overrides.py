"""One answer to "does this class still use the library's own implementation?"."""


def inherits_unchanged(cls: type, base: type, *names: str) -> bool:
    """Whether every named method resolves on ``cls`` to the same object it resolves to on ``base``.

    Library fast paths and defaults may stand in for a method only while it is
    the library's own; an adopter override, or a test patch on the subclass,
    keeps its call. The base is resolved at call time. Compare plain methods
    only: descriptors such as classmethods bind a fresh object per lookup.
    """
    return all(getattr(cls, name) is getattr(base, name) for name in names)
