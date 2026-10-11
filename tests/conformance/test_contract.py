"""Generated guarantees bind Due's actual PostgreSQL coordinator."""

from pytest_obligation import ObligationContract
from pytest_obligation.contract import Claim, NotApplicable, Profile, due_work_contract_suite

from due.django.conformance import DurableWorkBindings
from tests.conformance.bindings import admission, retries
from tests.harness.primitive import isolated_primitive_fixture as isolated_primitive_fixture
from tests.harness.primitive import primitive, record

bindings = DurableWorkBindings(primitive, record)

CONTRACT = ObligationContract(
    name="Due replay-safe work table",
    transactional=True,
    fixtures=("isolated_primitive",),
    profiles={
        Profile.A: Claim(),
        Profile.B: Claim(),
        Profile.C: NotApplicable(
            "This example is replay-safe; evidence-required operations have their own regression tests."
        ),
        Profile.D: Claim(),
        Profile.E: NotApplicable(
            "This example has an immutable target; projection revision tests exercise mutable targets."
        ),
        Profile.F: NotApplicable("This example admits explicit obligations and declares no fact-derived discovery."),
        Profile.G: NotApplicable("This example has no prerequisite gate; eligibility has separate regression tests."),
        Profile.HARMLESS_REPLAY: NotApplicable(
            "This example's external call returns a value without a remote mutation."
        ),
        Profile.INDIVISIBLE_ADMISSION: Claim(),
        Profile.JOB_RETRY_LIMITS: Claim(),
    },
    sweep=bindings.sweep,
    ownership=bindings.ownership,
    retention=bindings.retention,
    retry=retries,
    admission={"document publication": admission},
)


@due_work_contract_suite(CONTRACT)
class TestWorkTable:
    pass
