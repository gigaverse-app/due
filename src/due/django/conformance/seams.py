"""
One provider seam per adopter, from which the work-table suite derives its routine bindings.

Most work-table adopters bind the same few shapes by hand: enter the provider fake,
make a work row, observe the product and provider outcome, count provider calls,
and script a failure. A :class:`ProviderSeam` declares those facts once. From it,
:func:`seam_examples` derives ``delivery``, ``retry`` (bounded policies only),
``replay`` (from the mode's own effect method) and projection ``convergence``.
``outcome`` and ``rejection`` build the provider-outcome scenarios.

What stays explicit is product knowledge: producers through real entry points,
synchronous callers, rejection matrices, handoffs, gates and derivation facts.
Pass those, and any binding that must differ from the default, as overrides.

The derived bindings live here, so the per-binding authorship and annotation guards
do not see them. Two things keep them honest. Every derived callable that runs
production is the capability's own method, never adopter code. And the seam
declaration itself must carry the ARRANGE / REAL PRODUCTION / EXTERNAL SEAM /
OBSERVE annotations, so review still sees what is real and what is faked. The
suite's positive controls apply unchanged: an ``observe`` that ignores the provider,
a ``calls`` that never counts, or a ``refuse`` that never fails all fail their proofs.
"""

import ast
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, overload

from pytest_obligation.contract import (
    ObligationContractDesignError,
    missing_evidence_annotations,
)
from pytest_obligation.profiles.eventual_convergence import ConvergentWrite
from pytest_obligation.profiles.harmless_replay import ReplaySafeEffect as ReplayProof

from due.django.capabilities.authoring import DurableWorkCapability, ReplaySafeEffect
from due.django.capabilities.projection import DurableProjection
from due.django.capabilities.scheduled import DurableScheduledAction
from due.django.conformance.binding import entered_binding
from due.django.conformance.examples import (
    DeliveryExample,
    EffectExamples,
    FailureExample,
    RejectionExample,
)
from due.django.conformance.scenarios import (
    AdmissionFailureExample,
    ProducerExample,
    SynchronousCallerExample,
)
from due.django.models import UniverseDurableWork
from due.django.testing import hold_nudges
from due.errors import WorkFailureDisposition


def _declaration_source(filename: str, lineno: int) -> str:
    """The statement that constructs the seam, with the comment block directly above it."""
    lines = Path(filename).read_text(encoding="utf8").splitlines()
    tree = ast.parse("\n".join(lines))
    enclosing = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.stmt)
        and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.With, ast.Module))
        and node.lineno <= lineno <= (node.end_lineno or node.lineno)
    ]
    statement = min(enclosing, key=lambda node: (node.end_lineno or node.lineno) - node.lineno)
    start = statement.lineno - 1
    while start > 0 and lines[start - 1].strip().startswith("#"):
        start -= 1
    return "\n".join(lines[start : statement.end_lineno])


@dataclass(frozen=True, kw_only=True)
class ProviderSeam[W: UniverseDurableWork, P, O]:
    """How one adopter's provider is faked, counted and observed, declared once."""

    environment: Callable[[], AbstractContextManager[P]]
    """EXTERNAL SEAM: enter the provider fake and yield its handle; nothing else is replaced."""
    make_work: Callable[[], W]
    """ARRANGE: admit one work row through production admission; derived bindings hold its notification."""
    observe: Callable[[P, W], O]
    """OBSERVE: the product and provider outcome for this work, read without changing either."""
    delivered: Callable[[P, W], O]
    """The reviewed outcome one successful delivery of this work must reach; never read from the run."""
    calls: Callable[[P], int]
    """OBSERVE: provider calls at the effect boundary, for every work."""
    refuse: Callable[[P], object] | None = None
    """
    EXTERNAL SEAM: make later provider calls fail with an error production classifies as retryable.
    Return a context manager for scoped faults (for example ``ProviderProbe.reject``);
    callbacks that configure the provider in place may return a plain value.

    Required when the policy is bounded (max_rearms), which is when ``retry`` is derived.
    """
    calls_for: Callable[[P, W], int] | None = None
    """OBSERVE: provider calls for one work's identity; defaults to ``calls``."""
    provider_state: Callable[[P, W], Any] | None = None
    """
    OBSERVE: the provider-side result for one work, which a replay repeats; defaults to ``observe``.

    Declare it when ``observe`` reads product state the worker settles, since a replay
    repeats only the provider call and never settles the work.
    """
    replay_expected: Callable[[P, W], Any] | None = None
    """Independent provider outcome for replay, when its shape differs from ``delivered``.

    Defaults to ``delivered``. Derive this from the admitted input, never from
    observed output; for example compare the exact accepted payload while the
    delivery observation checks only a product completion flag.
    """
    arrange: Callable[[P, W], object] = field(default=lambda provider, work: None)
    """ARRANGE: provider-side state one delivery needs, such as a scripted receipt or stored source."""

    def __post_init__(self) -> None:
        # The caller of the generated __init__ is the declaration under review.
        frame = sys._getframe(2)
        source = _declaration_source(frame.f_code.co_filename, frame.f_lineno)
        missing = missing_evidence_annotations(source)
        if missing:
            raise ObligationContractDesignError(
                f"provider seam at {frame.f_code.co_filename}:{frame.f_lineno} is missing {missing}. "
                "Annotate the declaration so review can see what is production and what is faked."
            )

    def count_for(self, provider: P, work: W) -> int:
        return self.calls_for(provider, work) if self.calls_for is not None else self.calls(provider)

    def prepare(
        self,
        provider: P,
        *,
        script: Callable[[P, W], object] | None = None,
        make_work: Callable[[P], W] | None = None,
    ) -> W:
        """Admit without dispatch, then arrange the provider for the chosen history."""
        with hold_nudges():
            work = make_work(provider) if make_work is not None else self.make_work()
        (self.arrange if script is None else script)(provider, work)
        return work

    def delivery(self) -> AbstractContextManager[DeliveryExample[W, O]]:
        return self.outcome(self.arrange, self.delivered)()

    @contextmanager
    def retry(self) -> Iterator[FailureExample[W]]:
        assert self.refuse is not None, "seam_examples derives retry only from a seam that declares refuse"
        with entered_binding(self.environment) as provider:
            # A fault scope must remain active for every retry, and its cleanup
            # must not swallow the proof's failing verdict.
            with entered_binding(lambda: self.refuse(provider)):
                yield FailureExample(attempts=lambda work: self.count_for(provider, work))

    @overload
    def outcome(
        self,
        script: Callable[[P, W], object],
        expected: Callable[[P, W], O],
        *,
        observe: None = None,
        make_work: Callable[[P], W] | None = None,
    ) -> Callable[[], AbstractContextManager[DeliveryExample[W, O]]]: ...

    @overload
    def outcome[R](
        self,
        script: Callable[[P, W], object],
        expected: Callable[[P, W], R],
        *,
        observe: Callable[[P, W], R],
        make_work: Callable[[P], W] | None = None,
    ) -> Callable[[], AbstractContextManager[DeliveryExample[W, R]]]: ...

    def outcome(
        self,
        script: Callable[[P, W], object],
        expected: Callable[[P, W], Any],
        *,
        observe: Callable[[P, W], Any] | None = None,
        make_work: Callable[[P], W] | None = None,
    ) -> Callable[[], AbstractContextManager[DeliveryExample[W, Any]]]:
        """
        A delivery whose provider is scripted to answer differently: an uncertainty, or a rejection's core.

        ``script`` runs after the work exists and replaces ``arrange``. ``observe`` overrides the
        seam's observation when the outcome shows in other product fields. A
        context manager returned by ``script`` stays entered through the proof.
        """
        read: Callable[[P, W], Any] = observe if observe is not None else self.observe

        @contextmanager
        def binding() -> Iterator[DeliveryExample[W, Any]]:
            with entered_binding(self.environment) as provider, ExitStack() as scripts:
                # Keep a returned provider-fault scope active through delivery;
                # use the same failure-preserving owner as generated proofs.
                work = self.prepare(
                    provider,
                    script=lambda resource, work: scripts.enter_context(
                        entered_binding(lambda: script(resource, work))
                    ),
                    make_work=make_work,
                )
                yield DeliveryExample(
                    work=work,
                    observe=lambda: read(provider, work),
                    expected=expected(provider, work),
                    effect_calls=lambda: self.calls(provider),
                )

        return binding

    @overload
    def rejection(
        self,
        script: Callable[[P, W], object],
        expected: Callable[[P, W], O],
        disposition: WorkFailureDisposition,
        *,
        expected_attempts: int = 1,
        observe: None = None,
        make_work: Callable[[P], W] | None = None,
    ) -> Callable[[], AbstractContextManager[RejectionExample[W, O]]]: ...

    @overload
    def rejection[R](
        self,
        script: Callable[[P, W], object],
        expected: Callable[[P, W], R],
        disposition: WorkFailureDisposition,
        *,
        expected_attempts: int = 1,
        observe: Callable[[P, W], R],
        make_work: Callable[[P], W] | None = None,
    ) -> Callable[[], AbstractContextManager[RejectionExample[W, R]]]: ...

    def rejection(
        self,
        script: Callable[[P, W], object],
        expected: Callable[[P, W], Any],
        disposition: WorkFailureDisposition,
        *,
        expected_attempts: int = 1,
        observe: Callable[[P, W], Any] | None = None,
        make_work: Callable[[P], W] | None = None,
    ) -> Callable[[], AbstractContextManager[RejectionExample[W, Any]]]:
        delivery = self.outcome(script, expected, observe=observe, make_work=make_work)

        @contextmanager
        def binding() -> Iterator[RejectionExample[W, Any]]:
            with delivery() as example:
                yield RejectionExample(delivery=example, disposition=disposition, expected_attempts=expected_attempts)

        return binding

    def producer[A, R](
        self,
        arrange: Callable[[P], A],
        produce: Callable[[P, A], tuple[W, ...]],
        observe: Callable[[P, A], R],
        expected: R,
    ) -> Callable[[], AbstractContextManager[ProducerExample[W, R]]]:
        """
        A real admission path: arrange its product state, then admit through ``produce``.

        ``expected`` is the reviewed outcome after the admitted work is delivered.
        """

        @contextmanager
        def binding() -> Iterator[ProducerExample[W, R]]:
            with self.environment() as provider:
                arranged = arrange(provider)
                yield ProducerExample(
                    produce=lambda: produce(provider, arranged),
                    observe=lambda: observe(provider, arranged),
                    expected=expected,
                    effect_calls=lambda: self.calls(provider),
                )

        return binding

    def caller[A, R](
        self,
        arrange: Callable[[P], A],
        call: Callable[[P, A], object],
        observe: Callable[[P, A], R],
        expected: R,
        *,
        fail_effect: Callable[[P], AbstractContextManager[object]],
        failure: type[Exception] | None,
        requires_inline: bool = False,
    ) -> Callable[[], AbstractContextManager[SynchronousCallerExample[R]]]:
        """A request-path entry point that admits and then attempts inline."""

        @contextmanager
        def binding() -> Iterator[SynchronousCallerExample[R]]:
            with self.environment() as provider:
                arranged = arrange(provider)
                yield SynchronousCallerExample(
                    call=lambda: call(provider, arranged),
                    observe=lambda: observe(provider, arranged),
                    expected=expected,
                    effect_calls=lambda: self.calls(provider),
                    fail_effect=lambda: fail_effect(provider),
                    failure=failure,
                    requires_inline=requires_inline,
                )

        return binding

    @staticmethod
    def interrupted[R](
        producer: Callable[[], AbstractContextManager[ProducerExample[W, R]]], model: type[UniverseDurableWork]
    ) -> Callable[[], AbstractContextManager[AdmissionFailureExample[W, R]]]:
        """The same admission path, its connection lost right after a real ``model`` row is written."""

        @contextmanager
        def binding() -> Iterator[AdmissionFailureExample[W, R]]:
            with producer() as admission:
                yield AdmissionFailureExample.after_save(admission, model)

        return binding

    def replay(self, name: str, execute: Callable[[W], object]) -> Callable[[], AbstractContextManager[ReplayProof]]:
        @contextmanager
        def binding() -> Iterator[ReplayProof]:
            # EXTERNAL SEAM: the declared environment substitutes only the provider boundary.
            with self.environment() as provider:
                # ARRANGE: prepare admits real work through the adopter's annotated producer.
                # REAL PRODUCTION: execute is the capability's own effect method.
                # OBSERVE: provider_state and count_for read the independent provider ledger.
                yield ReplayProof(
                    name=name,
                    prepare=lambda: self.prepare(provider),
                    execute=execute,
                    observe=lambda work: (self.provider_state or self.observe)(provider, work),
                    execution_count_for=lambda work: self.count_for(provider, work),
                    expected=lambda work: (self.replay_expected or self.delivered)(provider, work),
                )

        return binding

    def convergence(
        self, projection: DurableProjection[Any, W]
    ) -> Callable[[], AbstractContextManager[ConvergentWrite]]:
        @contextmanager
        def binding() -> Iterator[ConvergentWrite]:
            # EXTERNAL SEAM: the annotated environment substitutes the provider boundary.
            with self.environment() as provider:
                # ARRANGE: prepare admits work through the adopter's producer.
                # REAL PRODUCTION: attempt_execution runs the registered projection.
                # OBSERVE: persisted revisions and the seam's independent product observation.

                def result(work_id: int) -> tuple[int, str, int, int | None, O]:
                    work = projection.model.objects.get(pk=work_id)
                    return (
                        work.pk,
                        work.state,
                        work.desired_revision,
                        work.applied_revision,
                        self.observe(provider, work),
                    )

                def admitted() -> int:
                    return self.prepare(provider).pk

                def settled() -> tuple[int, str, int, int | None, O]:
                    work_id = admitted()
                    projection.attempt_execution(work_id)
                    return result(work_id)

                def apply_evidence(state: tuple[int, str, int, int | None, O]) -> tuple[int, str, int, int | None, O]:
                    projection.attempt_execution(state[0])
                    return result(state[0])

                yield ConvergentWrite(
                    name=type(projection).__name__,
                    settled_state=settled,
                    unsettled_state=lambda: result(admitted()),
                    apply_evidence=apply_evidence,
                    is_settled=lambda state: state[3] == state[2],
                )

        return binding


def _replay_target[W: UniverseDurableWork](capability: DurableWorkCapability[W, Any]) -> Callable[[W], object] | None:
    """The mode's own effect method, which a blind replay repeats at the provider boundary."""
    if isinstance(capability, DurableProjection):
        return capability.write_projection
    if isinstance(capability, (ReplaySafeEffect, DurableScheduledAction)):
        return capability.perform_effect
    return None


def seam_examples[W: UniverseDurableWork](
    capability: DurableWorkCapability[W, Any], seam: ProviderSeam[W, Any, Any], **overrides: Any
) -> EffectExamples[W]:
    """
    Routine bindings derived from the seam; anything in ``overrides`` replaces or adds to them.

    ``retry`` is derived only for a bounded policy, ``replay`` only when the mode's
    effect method is the provider boundary, and ``convergence`` only for projections.
    Replay checks the independently declared ``delivered`` outcome as well as convergence.
    An adopter whose replay must repeat a lower layer or observe a different outcome
    passes ``replay=`` explicitly.
    """
    derived: dict[str, Any] = {"make_work": seam.make_work, "delivery": seam.delivery}
    if capability.policy.max_rearms is not None and "retry" not in overrides:
        if seam.refuse is None:
            raise ObligationContractDesignError(
                f"{type(capability).__name__}: a bounded policy exhausts work, so its seam must declare refuse"
            )
        derived["retry"] = seam.retry
    target = _replay_target(capability)
    if target is not None:
        derived["replay"] = seam.replay(type(capability).__name__, target)
    if isinstance(capability, DurableProjection):
        derived["convergence"] = seam.convergence(capability)
    return EffectExamples(**{**derived, **overrides})
