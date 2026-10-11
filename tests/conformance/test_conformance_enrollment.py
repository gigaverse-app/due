"""
Enrollment is mandatory; declarations cannot waive production semantics.

Design and proof boundaries: docs/architecture.md#verification.
See the guide's verification module map for related production-bound suites.
"""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from django.conf import settings
from django.test import override_settings
from pytest_obligation.contract import ObligationContractDesignError

from due.django.capabilities.compensation import IndependentCompensation
from due.django.capabilities.operations import DurableExternalOperation
from due.django.capabilities.projection import DurableProjection
from due.django.capabilities.scheduled import DurableScheduledAction
from due.django.conformance.composition import compensations_for
from due.django.conformance.enrollment import (
    EffectEnrollment,
    assert_complete_enrollment,
    discover_enrollment,
    enrolled_effects,
)
from due.django.conformance.suite import (
    EffectExamples,
    assert_registered_schedule,
    effect_contract,
)
from due.django.public import (
    DurableWorkCapability,
    OwnedDurableWorkCapability,
    ReplaySafeEffect,
)
from due.django.runtime.registry import installed
from due.django.runtime.tasks import capability_for
from due.django.testing import hold_nudges
from tests.harness.enrollment import TEST_OWNED_CAPABILITIES, no_fixture
from tests.harness.primitive import (
    isolated_primitive_fixture as isolated_primitive_fixture,
)
from tests.harness.primitive import (
    primitive,
    record,
)
from tests.harness.primitive import (
    root_registration_fixture as root_registration_fixture,
)

PRODUCTION_CAPABILITIES = {entry.name: entry.capability for entry in installed.get().values()}


def test_every_registered_capability_has_one_collectable_production_bound_suite():
    root = (
        next(parent for parent in Path(__file__).resolve().parents if (parent / "pyproject.toml").is_file()) / "tests"
    )
    assert root.is_dir()
    assert_complete_enrollment(PRODUCTION_CAPABILITIES, discover_enrollment(root))


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unregistered"])
def test_enrollment_inventory_rejects_missing_duplicate_or_unregistered_capabilities(fault, monkeypatch):
    from tests.harness.primitive import primitive

    other = replace(primitive)
    monkeypatch.setattr(
        "due.django.conformance.enrollment.import_string",
        lambda path: primitive if path == "production.capability" else other,
    )
    entry = EffectEnrollment("production.capability", Path("test_domain.py"), "TestDomain")
    found = {
        "missing": [],
        "duplicate": [entry, entry],
        "unregistered": [replace(entry, capability_path="another.capability")],
    }[fault]
    with pytest.raises(AssertionError, match="conformance enrollment"):
        assert_complete_enrollment({"production": primitive}, found)


@pytest.mark.parametrize(
    "disabled", ["@pytest.mark.skip(reason='later')", "@pytest.mark.xfail(reason='later')", "__test__ = False"]
)
def test_disabled_class_cannot_satisfy_enrollment(tmp_path, disabled):
    path = tmp_path / "test_domain.py"
    text = dedent("""
        import pytest
        from production import capability
        from due.django.conformance.suite import effect_contract_suite
    """)
    if disabled.startswith("@"):
        text += disabled + "\n"
    text += "@effect_contract_suite(capability, examples=examples)\nclass TestDomain:\n"
    text += "    " + (disabled if not disabled.startswith("@") else "pass") + "\n"
    path.write_text(text)
    with pytest.raises(AssertionError, match="disabled|skipped"):
        enrolled_effects(path)


@pytest.mark.parametrize("fault", ["name", "queue", "cadence", "expires", "duplicate", "missing"])
def test_installed_scheduler_drift_is_detected_without_calling_the_schedule_generator(fault):
    entry = installed.get()["primitive_self_test"]
    schedule = {
        name: {**item, "options": dict(item.get("options", {}))} for name, item in settings.CELERY_BEAT_SCHEDULE.items()
    }
    key = f"recover_{entry.name}"
    if fault == "name":
        schedule[key]["args"] = ("event_room_shutdown",)
    elif fault == "queue":
        schedule[key]["options"]["queue"] = "wrong"
    elif fault == "cadence":
        schedule[key]["schedule"] = 61
    elif fault == "expires":
        schedule[key]["options"]["expire_seconds"] = 999
    elif fault == "duplicate":
        schedule["duplicate"] = schedule[key]
    else:
        del schedule[key]
    with override_settings(CELERY_BEAT_SCHEDULE=schedule), pytest.raises(AssertionError, match=entry.name):
        assert_registered_schedule(entry)


def test_collected_class_cannot_omit_an_independent_or_composed_proof():
    from due.django.conformance.collected_effects import assert_collected_proofs

    expected = {"A-recovery", "AF-composition", "compensation-after-exhaustion"}
    assert_collected_proofs(expected, expected, "TestExample")
    for missing in expected:
        with pytest.raises(AssertionError, match="lost collected proofs"):
            assert_collected_proofs(expected, expected - {missing}, "TestExample")


def test_parallel_worker_report_preserves_selection_and_failure_evidence():
    from due.django.conformance.collected_effects import CollectedEffect
    from due.django.conformance.reporting import merge_worker_reports

    def report(outcomes):
        return CollectedEffect(
            capability="production.effect",
            suite="domain.TestEffect",
            contract="A/B",
            production_policy={},
            bindings={},
            proof_sources={},
            unassessed=[],
            required_by={},
            expected=["first", "second"],
            collected=["first", "second"],
            selected=["first", "second"],
            outcomes=outcomes,
        )

    combined = {"effect": report({"first": ["passed"]})}
    merge_worker_reports(combined, {"effect": report({"second": ["failed"]})})
    assert combined["effect"]["selected"] == ["first", "second"]
    assert combined["effect"]["outcomes"] == {"first": ["passed"], "second": ["failed"]}


def test_identical_but_unregistered_capability_copy_is_not_enrollment():
    from due.django.conformance.bindings import DurableWorkBindings

    capability = capability_for("primitive_self_test")
    copy = replace(capability)
    bindings = DurableWorkBindings(copy, no_fixture)
    with pytest.raises(ValueError, match="registered production capability"):
        _ = bindings.registration


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("rearms", [1, 5])
@pytest.mark.usefixtures("isolated_primitive")
def test_shared_bindings_follow_nondefault_retry_and_retention_policy(rearms):

    from pytest_obligation.profiles.durable_retention import (
        assert_retention_prunes_settled_work,
    )

    from due.django.conformance.bindings import DurableWorkBindings

    capability = capability_for("primitive_self_test")
    changed = replace(
        capability,
        policy=replace(
            capability.policy, max_rearms=rearms, retry_delay=timedelta(seconds=17), retention=timedelta(days=31)
        ),
    )

    make_work = record

    with hold_nudges():
        bindings = DurableWorkBindings(changed, make_work)
        ownership = bindings.ownership()
        identity = ownership.make_claimable()
        assert ownership.claim() is not None
        for attempt in range(rearms + 1):
            ownership.expire_lease(identity)
            ownership.reclaim_stalled(identity)
            next_claim = ownership.claim()
            assert (next_claim is None) == (attempt == rearms)
        assert_retention_prunes_settled_work(bindings.retention())


# Independent literal: weakening a harness requirement must fail this inventory,
# even with no production applications installed in the root branch.


EXPECTED_REQUIREMENTS: dict[type[DurableWorkCapability[Any, Any]], tuple[str, ...]] = {
    ReplaySafeEffect: ("delivery", "retry", "replay"),
    DurableExternalOperation: ("delivery", "retry", "applied_receipt", "receipt_repair", "rejections", "uncertainties"),
    DurableScheduledAction: ("delivery", "retry", "replay", "snapshot"),
    DurableProjection: ("delivery", "retry", "replay", "snapshot"),
    IndependentCompensation: ("delivery", "retry", "replay"),
    OwnedDurableWorkCapability: ("delivery", "retry", "replay"),
}


def _expected_requirements(capability: DurableWorkCapability[Any, Any]) -> tuple[str, ...]:
    mode = next(mode for mode in EXPECTED_REQUIREMENTS if isinstance(capability, mode))
    required = EXPECTED_REQUIREMENTS[mode]
    if capability.policy.max_rearms is not None:
        required += ("terminal",)
    if isinstance(capability, OwnedDurableWorkCapability) and capability.discovery is not None:
        required += ("derivation",)
        if not isinstance(capability, DurableExternalOperation):
            required += ("snapshot",)
    if compensations_for(capability):
        required += ("compensation",)
    if capability.eligibility is not None:
        required += ("eligibility",)
    return tuple(dict.fromkeys(required))


def missing_requirement_cases():
    """Remove each independently required proof from every installed capability."""
    capabilities = [*TEST_OWNED_CAPABILITIES, *PRODUCTION_CAPABILITIES.values()]
    cases = []
    for capability in capabilities:
        for requirement in _expected_requirements(capability):
            name = f"{type(capability).__name__}-without-{requirement}"
            cases.append(pytest.param(capability, requirement, id=name))
    return cases


@pytest.mark.parametrize(("capability", "required"), missing_requirement_cases())
@pytest.mark.no_db
def test_capabilities_require_their_examples(capability, required, monkeypatch):
    from due.django.runtime.registry import DurableWorkRegistration

    # One route at a time permits mode fixtures to share test-owned tables.
    entry = DurableWorkRegistration("enrollment_negative", capability)
    monkeypatch.setitem(installed.get(), entry.name, entry)
    needed = _expected_requirements(capability)
    examples = EffectExamples(
        make_work=no_fixture,
        immutable_inputs="An immutable domain occurrence.",
        delivery=no_fixture,
        retry=no_fixture,
        replay=no_fixture if "replay" in needed and required != "replay" else None,
        terminal=no_fixture if "terminal" in needed and required != "terminal" else None,
        snapshot=no_fixture if "snapshot" in needed and required != "snapshot" else None,
        applied_receipt=no_fixture if "applied_receipt" in needed and required != "applied_receipt" else None,
        receipt_repair=no_fixture if "receipt_repair" in needed and required != "receipt_repair" else None,
        rejections={"rejected": no_fixture} if "rejections" in needed and required != "rejections" else None,
        uncertainties={"unknown": no_fixture} if "uncertainties" in needed and required != "uncertainties" else None,
        derivation=no_fixture if "derivation" in needed and required != "derivation" else None,
        compensation=no_fixture if "compensation" in needed and required != "compensation" else None,
    )
    # Deliberate invalid declaration: even a dynamically supplied missing field must fail collection.
    examples = replace(examples, **{required: None})  # pyrefly: ignore[bad-argument-type]
    with pytest.raises(ObligationContractDesignError, match=required):
        effect_contract(capability, examples=examples)


@pytest.mark.no_db
def test_expected_requirements_cover_every_mode_and_match_the_harness():
    from due.django.conformance.suite import MODES, required_examples_for

    assert set(EXPECTED_REQUIREMENTS) == set(MODES)
    assert {mode for mode in EXPECTED_REQUIREMENTS if any(isinstance(c, mode) for c in TEST_OWNED_CAPABILITIES)} == set(
        MODES
    )
    for mode, required in EXPECTED_REQUIREMENTS.items():
        assert required_examples_for(mode) == required


@pytest.mark.no_db
@pytest.mark.parametrize("selection", ["repository", "domain", "file", "node"])
def test_selected_inventory_keeps_hidden_classes_in_every_selector_scope(tmp_path: Path, selection: str) -> None:
    from due.django.conformance.enrollment import selected_enrollment

    root = tmp_path / "tests"
    root.mkdir()
    path = root / "test_domain.py"
    path.write_text(
        dedent("""
        from production import capability
        from due.django.conformance.suite import effect_contract_suite
        @effect_contract_suite(capability)
        class TestHidden:
            def __init__(self): pass
    """)
    )
    selectors = {
        "repository": str(tmp_path),
        "domain": str(root),
        "file": str(path),
        "node": str(path) + "::TestHidden",
    }
    found = selected_enrollment(root, [selectors[selection]])
    assert [(entry.module, entry.class_name) for entry in found] == [(path, "TestHidden")]


def declared_only(*args: object, **kwargs: object) -> Any:
    # ARRANGE: never entered; only the contract declaration is under test.
    # REAL PRODUCTION: none runs, so no binding can be mistaken for a proof.
    # EXTERNAL SEAM: none.
    # OBSERVE: the generated dispositions and case IDs.
    raise AssertionError("a declaration-only binding must not execute")


def _immutable_examples(**declared: Any) -> EffectExamples:
    """A complete immutable-input declaration for the root primitive, plus the decisions under test."""
    assert _expected_requirements(primitive) == ("delivery", "retry", "replay", "terminal")
    return replace(
        EffectExamples(
            make_work=declared_only,
            immutable_inputs="An immutable domain occurrence.",
            delivery=declared_only,
            retry=declared_only,
            replay=declared_only,
            terminal=declared_only,
        ),
        **declared,
    )


def _case_ids(examples: EffectExamples) -> set[str]:
    from pytest_obligation.contract import contract_cases

    return {param.id for param in contract_cases(effect_contract(primitive, examples=examples))}


@pytest.mark.no_db
def test_single_row_admission_is_reported_not_applicable_instead_of_unassessed():
    from pytest_obligation.contract import NotApplicable, NotAssessed, Profile

    reason = "Admission is one compensation row reserved inside its producer's claim."
    contract = effect_contract(primitive, examples=_immutable_examples(admission_not_applicable=reason))
    assert contract.profiles[Profile.I] == NotApplicable(reason)
    assert {"I-not_applicable"} <= _case_ids(_immutable_examples(admission_not_applicable=reason))
    # The default stays visible debt: nothing declared is not a decision.
    assert isinstance(effect_contract(primitive, examples=_immutable_examples()).profiles[Profile.I], NotAssessed)


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("declared", "match"),
    [
        (
            {"admission_not_applicable": "Admission is one work row.", "admission_failures": {"fault": declared_only}},
            "contradicts the admission_failures",
        ),
        ({"admission_not_applicable": "n/a"}, "must name the single atomic work-row write"),
    ],
    ids=["with-a-bound-failure", "placeholder-reason"],
)
def test_single_row_admission_rejects_contradictory_or_empty_declarations(declared, match):
    with pytest.raises(ObligationContractDesignError, match=match):
        effect_contract(primitive, examples=_immutable_examples(**declared))


@pytest.mark.no_db
def test_convergence_binding_and_family_decisions_reach_the_contract():
    from pytest_obligation.contract import (
        Claim,
        NotApplicable,
        NotAssessed,
        Profile,
        convergence_assessments,
    )
    from pytest_obligation.profiles.catalog import ConvergenceFamily

    decision = NotApplicable("No provider evidence is ever applied to this work.")
    examples = _immutable_examples(
        convergence=declared_only, convergence_families={ConvergenceFamily.EVIDENCE_CONFLUENCE: decision}
    )
    contract = effect_contract(primitive, examples=examples)
    assert isinstance(contract.profiles[Profile.E], Claim), "a bound result application claims E"
    families = convergence_assessments(contract)
    assert isinstance(families[ConvergenceFamily.MONOTONIC_RESULTS], Claim)
    assert families[ConvergenceFamily.EVIDENCE_CONFLUENCE] == decision
    # An undeclared, unbound sibling is never certified by the others.
    assert isinstance(families[ConvergenceFamily.IN_FLIGHT], NotAssessed)
    ids = _case_ids(examples)
    assert "E-assert_convergence_bindings_are_production_bound" in ids
    assert "E-in_flight-not_assessed" in ids
    assert not {"E-convergence-not_assessed", "E-evidence_confluence-not_assessed"} & ids


@pytest.mark.no_db
@pytest.mark.parametrize(
    ("bound", "decision", "match"),
    [
        (False, "not_applicable", "convergence_families decide families of a profile E it never claims"),
        (True, "decline", "needs NotApplicable with its domain reason"),
    ],
    ids=["e-never-claimed", "not-a-not-applicable"],
)
def test_convergence_family_decisions_reject_unclaimed_or_untyped_declarations(bound, decision, match):
    from pytest_obligation.contract import Decline, NotApplicable
    from pytest_obligation.profiles.catalog import ConvergenceFamily

    reason = "No work is ever paused across a competing change."
    families = {ConvergenceFamily.IN_FLIGHT: NotApplicable(reason) if decision == "not_applicable" else Decline(reason)}
    declared: dict[str, Any] = {"convergence": declared_only} if bound else {}
    with pytest.raises(ObligationContractDesignError, match=match):
        effect_contract(primitive, examples=_immutable_examples(**declared, convergence_families=families))


def _gated_contract(monkeypatch: pytest.MonkeyPatch):
    from datetime import timedelta

    from django.db.models import Q

    from due.django.conformance.suite import DeliveryExample, EligibilityExample
    from due.django.public import ExecutionEligibility
    from tests.harness.gated import GatedPrimitiveEffect
    from tests.harness.primitive import PrimitiveEffect, PrimitiveOwner

    capability = replace(
        GatedPrimitiveEffect.for_owner(owner=PrimitiveOwner, work=primitive.model, policy=primitive.policy),
        eligibility=ExecutionEligibility(lambda now: Q(owner__value="ready"), recheck_after=timedelta(seconds=30)),
    )
    entry = installed.get()["primitive_self_test"]
    monkeypatch.setitem(installed.get(), entry.name, replace(entry, capability=capability))

    def example():
        work = record()

        def make_eligible() -> None:
            PrimitiveOwner.objects.filter(pk=work.owner_id).update(value="ready")

        return EligibilityExample(
            delivery=DeliveryExample(
                work=work,
                observe=lambda: PrimitiveOwner.objects.get(pk=work.owner_id).value,
                expected=work.payload,
                effect_calls=lambda: len(PrimitiveEffect.calls),
            ),
            make_eligible=make_eligible,
        )

    return effect_contract(capability, examples=_immutable_examples(eligibility=example))


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
def test_generated_gate_proof_detects_missing_runtime_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from pytest_obligation.contract import contract_cases

    from due.django.public import ExecutionEligibility

    contract = _gated_contract(monkeypatch)
    case = next(
        param.values[0]
        for param in contract_cases(contract)
        if param.id == "eligibility-assert_periodic_inspection_is_bounded"
    )
    # Mutate the production enforcement, preserving its declaration and the
    # real exporter/registration. The old eligibility history missed this.
    monkeypatch.setattr(
        ExecutionEligibility, "filter", lambda self, queryset, *, now: queryset.filter(self.condition(now))
    )
    with pytest.raises(AssertionError, match="periodic inspection never becomes due"):
        case.run()


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize("syntax", ["update", "cte"], ids=["same-value-update", "cte-returning-select"])
def test_generated_gate_proof_detects_writes_hidden_by_unchanged_snapshots(
    monkeypatch: pytest.MonkeyPatch, syntax: str
) -> None:
    from django.db import connection
    from pytest_obligation.contract import contract_cases

    from due.django.public import ExecutionEligibility

    contract = _gated_contract(monkeypatch)
    case = next(
        param.values[0]
        for param in contract_cases(contract)
        if param.id == "eligibility-assert_blocked_gate_preserves_intent"
    )
    original = ExecutionEligibility.filter

    def write_then_filter(self, queryset, *, now):
        # Real PostgreSQL writes deliberately preserve every application/work
        # value. The CTE reports SELECT, defeating a SQL-prefix-only observer.
        table = connection.ops.quote_name(queryset.model._meta.db_table)
        update = f"UPDATE {table} SET updated_at = updated_at"
        with connection.cursor() as cursor:
            cursor.execute(
                update if syntax == "update" else f"WITH touched AS ({update} RETURNING id) SELECT id FROM touched"
            )
        return original(self, queryset, now=now)

    monkeypatch.setattr(ExecutionEligibility, "filter", write_then_filter)
    with pytest.raises(AssertionError, match="blocked work mutated durable state"):
        case.run()


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("isolated_primitive")
@pytest.mark.parametrize(
    "proof",
    [
        "assert_gate_bindings_are_production_bound",
        "assert_periodic_inspection_is_bounded",
        "assert_blocked_gate_preserves_intent",
        "assert_eligible_gate_recovers_lost_notification",
    ],
)
def test_generated_gate_proofs_accept_the_unmodified_runtime(monkeypatch: pytest.MonkeyPatch, proof: str) -> None:
    from pytest_obligation.contract import contract_cases

    contract = _gated_contract(monkeypatch)
    case = next(param.values[0] for param in contract_cases(contract) if param.id == f"eligibility-{proof}")
    case.run()
