"""Validate the static suite inventory against registered production objects."""

from collections import Counter
from collections.abc import Mapping

from django.utils.module_loading import import_string

from due._suite_inventory import (
    EffectEnrollment as EffectEnrollment,
)
from due._suite_inventory import (
    discover_enrollment as discover_enrollment,
)
from due._suite_inventory import (
    enrolled_effects as enrolled_effects,
)
from due._suite_inventory import (
    selected_enrollment as selected_enrollment,
)
from due.django.capabilities.authoring import DurableWorkCapability
from due.django.models import UniverseDurableWork


def assert_complete_enrollment[WorkT: UniverseDurableWork, ResultT](
    expected: Mapping[str, DurableWorkCapability[WorkT, ResultT]], found: list[EffectEnrollment]
) -> None:
    """
    Resolve only production imports and require the exact registered objects.

    The AST inventory never imports test modules. Aliases of one capability are
    still duplicates; a structurally identical replacement is not enrollment.
    """
    routes = {id(capability): name for name, capability in expected.items()}
    counts: Counter[str] = Counter()
    unrelated = []
    for entry in found:
        capability = import_string(entry.capability_path)
        name = routes.get(id(capability))
        if name is None:
            unrelated.append(entry.capability_path)
        else:
            counts[name] += 1
    missing = set(expected) - counts.keys()
    duplicate = {name for name, count in counts.items() if count != 1}
    assert not (missing or unrelated or duplicate), (
        f"Durable-effect conformance enrollment: missing={sorted(missing)}, "
        f"unregistered={sorted(unrelated)}, duplicate={sorted(duplicate)}. "
        "Declare one effect_contract_suite using the registered production capability and domain examples."
    )
