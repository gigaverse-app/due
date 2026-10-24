"""These classes collect real tests, selected by each capability's policies."""

from due.django.conformance.suite import effect_contract_suite

from acme.effects import cleanup_spool, order_printing, project_portal, publish_proof

from .contracts import cleanup, portal, printer, proof


@effect_contract_suite(publish_proof, examples=proof.EXAMPLES)
class TestProofContract:
    pass


@effect_contract_suite(project_portal, examples=portal.EXAMPLES)
class TestPortalContract:
    pass


@effect_contract_suite(order_printing, examples=printer.EXAMPLES)
class TestPrinterContract:
    pass


@effect_contract_suite(cleanup_spool, examples=cleanup.EXAMPLES)
class TestCleanupContract:
    pass
