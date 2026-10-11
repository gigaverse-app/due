"""Explicit owner-to-work field capture inside the existing producer lock.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from django.db import models


@dataclass(frozen=True, init=False)
class Capture:
    """An immutable field declaration reusable by different work models."""

    paths: Mapping[str, str]
    prefetch: bool

    def __init__(self, *, prefetch: bool = False, **paths: str) -> None:
        object.__setattr__(self, "paths", MappingProxyType(dict(paths)))
        object.__setattr__(self, "prefetch", prefetch)

    @staticmethod
    def _relations(owner: type[models.Model], path: str) -> tuple[str, ...]:
        parts = path.split(".")
        current = owner
        relations = []
        for index, name in enumerate(parts):
            field = current._meta.get_field(name)
            if not field.concrete or field.many_to_many or field.one_to_many:
                raise ValueError(f"capture requires a concrete forward field: {path}")
            if index < len(parts) - 1:
                if not isinstance(field, models.ForeignKey):
                    raise ValueError(f"capture path must traverse a forward relation: {path}")
                relations.append("__".join(parts[: index + 1]))
                current = field.remote_field.model
        return tuple(relations)

    def validate(self, owner: type[models.Model], domain_fields: set[str]) -> None:
        for name, path in self.paths.items():
            if name not in domain_fields:
                raise ValueError(f"capture destination must be a domain field: {name}")
            self._relations(owner, path)

    def read(self, owner: models.Model, **overrides: object) -> dict[str, object]:
        paths = {name: path for name, path in self.paths.items() if name not in overrides}
        relations = {relation for path in paths.values() for relation in self._relations(type(owner), path)}
        source = owner
        if relations:
            if not self.prefetch:
                raise ValueError("related capture paths require prefetch=True")
            source = type(owner)._default_manager.select_related(*sorted(relations)).get(pk=owner.pk)
        values: dict[str, object] = {}
        for name, path in paths.items():
            current = source
            parts = path.split(".")
            for component in parts[:-1]:
                # Paths are validated forward fields and prepared above through
                # select_related. Dynamic access consumes that explicit contract.
                related = getattr(current, component)
                if related is None:
                    raise ValueError(f"capture path {path} crosses a null relation")
                current = related
            field = current._meta.get_field(parts[-1])
            assert isinstance(field, models.Field), "capture validated a concrete field"
            if field.attname in current.get_deferred_fields():
                raise ValueError(f"capture field {path} is deferred")
            values[name] = field.value_from_object(current)
        return {**values, **overrides}
