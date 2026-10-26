"""Validated external service-result storage for evidence-required operations.

See docs/architecture.md for Due's guarantees and integration boundaries.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property

from django.db import models
from pydantic import JsonValue, TypeAdapter

from due.django.models import EffectAttempt


class StoredAppliedEffectEvidence(models.Model):
    """Canonical receipt identity; subclasses may override result with a native storage field."""

    attempt = models.OneToOneField(EffectAttempt, on_delete=models.PROTECT, primary_key=True)
    result = models.JSONField()

    class Meta:
        abstract = True


class AppliedEffectEvidenceStore[ResultT](ABC):
    @abstractmethod
    def record(self, attempt: EffectAttempt, result: ResultT) -> None:
        """Persist exact evidence; identical results are safe, contradictions fail."""

    @abstractmethod
    def read(self, attempt: EffectAttempt) -> ResultT:
        """Read canonical evidence for the captured attempt; no external I/O."""

    def record_and_read(self, attempt: EffectAttempt, result: ResultT) -> ResultT:
        """Persist and return canonical evidence; stores may reuse their locked read.

        The fallback preserves existing custom stores implementing record/read.
        The caller must keep the evidence lock across this operation.
        """
        self.record(attempt, result)
        return self.read(attempt)

    def retains(self, stored: ResultT, received: ResultT) -> bool:
        """Whether ``stored`` is what this store keeps for ``received``; equality unless it canonicalizes."""
        return stored == received


@dataclass(frozen=True)
class CanonicalAppliedEffectEvidenceStore[ResultT, PayloadT](AppliedEffectEvidenceStore[ResultT]):
    """Retain the first canonical receipt; only explicit domain policy relaxes equality."""

    model: type[StoredAppliedEffectEvidence]
    equivalent: Callable[[ResultT, ResultT], bool] | None = field(default=None, kw_only=True)

    @abstractmethod
    def encode(self, result: ResultT) -> PayloadT:
        """Serialize the canonical provider type to its existing storage format."""

    @abstractmethod
    def decode(self, payload: PayloadT) -> ResultT:
        """Read and validate the same canonical provider type."""

    def record(self, attempt: EffectAttempt, result: ResultT) -> None:
        payload = self.encode(result)
        receipt, created = self.model.objects.get_or_create(attempt=attempt, defaults={"result": payload})
        if created:
            return
        # get_or_create already read the winning row. Decode that immutable
        # payload directly; duplicate evidence must not pay for a second SELECT.
        first = self.decode(receipt.result)
        # Evolving provider state may still identify the same occurrence. The
        # adopter must explicitly establish equivalence; storage never guesses
        # which fields are identity. Even equivalent evidence keeps the first row.
        matches = (
            self.equivalent(first, self.decode(payload))
            if self.equivalent is not None
            else self.encode(first) == payload
        )
        if not matches:
            raise ValueError("provider receipt contradicts earlier evidence for this attempt")

    def retains(self, stored: ResultT, received: ResultT) -> bool:
        # The canonical encoding may redact or normalize (an echoed upload credential,
        # field order); it is also what record() compares, so it defines the same evidence.
        return self.encode(stored) == self.encode(received)

    def read(self, attempt: EffectAttempt) -> ResultT:
        # The Django field may be JSON or binary; decoding is the typed boundary.
        return self.decode(self.model.objects.values_list("result", flat=True).get(attempt=attempt))


@dataclass(frozen=True)
class JsonAppliedEffectEvidenceStore[ResultT](CanonicalAppliedEffectEvidenceStore[ResultT, JsonValue]):
    result_type: type[ResultT]

    @cached_property
    def adapter(self) -> TypeAdapter[ResultT]:
        return TypeAdapter(self.result_type)

    def encode(self, result: ResultT) -> JsonValue:
        return self.adapter.dump_python(self.adapter.validate_python(result), mode="json")

    def decode(self, payload: JsonValue) -> ResultT:
        return self.adapter.validate_python(payload)


@dataclass(frozen=True)
class BinaryAppliedEffectEvidenceStore[ResultT](CanonicalAppliedEffectEvidenceStore[ResultT, bytes]):
    serialize: Callable[[ResultT], bytes]
    deserialize: Callable[[bytes], ResultT]

    def encode(self, result: ResultT) -> bytes:
        return self.serialize(result)

    def decode(self, payload: bytes) -> ResultT:
        # PostgreSQL drivers can expose BinaryField values as memoryview.
        return self.deserialize(bytes(payload))
