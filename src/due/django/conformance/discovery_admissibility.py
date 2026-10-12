"""
Every row the production discovery query can return must be admissible.

Profile F proves discovery on the adapter's own derivation examples: implied,
moved and stopped owners, every one of them a row the product's validated
writers would create. It never asks what else the candidate query returns. The
database holds rows no validated writer creates today: ``blank=False`` is
enforced only when a model is validated, so a legacy import, a migration or a
bare ``update()`` leaves ``file=''`` in a NOT NULL column. Discovery reads those
rows as readily as any other.

That is the PR #5297 incident. Legacy ``CommunityPicture`` and
``CommunityBackgroundPicture`` rows with ``file=''`` matched
``RenditionPolicy.missing_work()``; ``desired_for`` built work with
``source_key=''``; ``request_work`` rejected it in ``full_clean``; and
``OwnedDurableWorkCapability.derive()`` raised on every tick. On dev that was
~2,870 errors a day from 2026-10-05, and the 86 valid images queued behind the
first bad row were never enrolled. Every profile stayed green.

These histories are generated for every registered capability with a
``Discovery``. Each one takes a valid implied owner from the adapter's
derivation examples and writes one database-legal edge value into one owner
field, the way the legacy rows got there: an ``update()`` that skips model
validation. Then production decides:

1. **Every database-legal candidate is admissible.** The row is either excluded
   by the production candidate query, or the production ``derive()`` admits it
   as outstanding work. A raised ``derive()`` and a candidate that
   ``desired_for`` declines both fail: the first stops recovery of its kind
   every tick, the second is re-read every tick forever and occupies a page
   slot that a valid owner needs.
2. **An inadmissible candidate does not block the one behind it.** With the
   edge row first in candidate order, the valid owner behind it is still
   enrolled by the same tick. This is the liveness half: it fails on a page
   loop that aborts at its first failure, whatever the edge value, and holds
   for an isolating loop even while (1) still reports the bad row. PR #5298's
   ``test_one_poisoned_identity_does_not_stall_recovery[discovery-*]`` proves
   the same isolation with an injected failure; this case uses a row the
   database can actually contain.

**Which fields.** Every concrete, non-null, non-relational owner field with a
cheap edge value, not only the fields the work captures. No production
discovery declares that mapping: ``RenditionPolicy.desired`` copies
``picture.file.name`` into ``source_key`` without a ``capture=``. The harness
cannot see which owner fields feed the work, so it perturbs each one and lets
the production query and ``derive()`` decide. An edge value the database itself
rejects (a CHECK constraint) cannot exist in a row, and its case passes.

**What this cannot see.** Edge values beyond the catalog in ``edge_values.py``,
combinations of fields, nullable fields set to ``None``, edge values in related
rows the query joins or ``desired_for`` reads (a community's universe, a post's
platform account), and owner rows a real legacy writer left in states the
adapter's ``make_implied`` never reaches. A green case is evidence for that
field and that value, not for every row in a production table. Native recovery
sweeps get the same catalog through
``profiles/automatic_recovery/owed_row_admissibility.py``.
"""

from collections.abc import Callable
from functools import partial
from typing import Any

from pytest_obligation.integrations.django.edge_values import CandidateEdge, model_edges, write_unvalidated
from pytest_obligation.integrations.django.selection_completeness import CompletenessBinding

from due.django.public import Discovery, OwnedDurableWorkCapability, database_now
from due.django.runtime.coordination import outstanding_work


def candidate_edges(capability: OwnedDurableWorkCapability[Any, Any, Any]) -> tuple[CandidateEdge, ...]:
    """Every generated case for one capability; suite and collection checks share this list."""
    return model_edges(capability.owner_model)


def _discovery_of(capability: OwnedDurableWorkCapability[Any, Any, Any]) -> Discovery[Any, Any]:
    discovery = capability.discovery
    assert discovery is not None, f"{capability.model._meta.label_lower}: admissibility requires a Discovery"
    return discovery


def _candidates(capability: OwnedDurableWorkCapability[Any, Any, Any], owners: tuple[Any, ...]) -> list[Any]:
    """The owners, in production candidate order, on the page the next production derive() reads."""
    discovery = _discovery_of(capability)
    page = discovery.bounded_candidates(now=database_now()).values_list("pk", flat=True)[: discovery.page_size]
    return [owner for owner in page if owner in owners]


def _enrolled(capability: OwnedDurableWorkCapability[Any, Any, Any], owner_id: Any) -> bool:
    return outstanding_work(capability.model).filter(**{capability.owner_attname: owner_id}).exists()


def assert_database_legal_candidate_is_admissible(
    capability: OwnedDurableWorkCapability[Any, Any, Any], make_implied: Callable[[], Any], edge: CandidateEdge
) -> None:
    """INVARIANT: the candidate query selects only rows production derive() can admit."""
    name = capability.model._meta.label_lower
    owner_id = make_implied()
    # POSITIVE CONTROL: the unmodified fixture is a candidate, so an absent edge
    # row below means the query excluded it, not that nothing was ever selected.
    assert _candidates(capability, (owner_id,)) == [owner_id], (
        f"{name}: the derivation example's implied owner is not a discovery candidate; profile F is the one to read"
    )
    # An excluded row, or a value the database rejects, still runs the same tick:
    # every case then requires a clean derive(), so no path passes without one.
    selected = write_unvalidated(capability.owner_model, owner_id, edge) and bool(_candidates(capability, (owner_id,)))
    try:
        capability.derive()
    except Exception as error:
        if not selected:
            raise AssertionError(
                f"{name}: derive() raised {error!r} in a tick where the owner with {edge.id} is not a candidate. "
                f"The derivation example or its environment is broken, not the edge row"
            ) from error
        raise AssertionError(
            f"{name}: discovery selects a database-legal owner with {edge.id} ({edge.value!r}) that production "
            f"cannot admit, and derive() raised {error!r}. Every recovery tick of this kind now fails on that "
            f"row. Exclude the row in the candidate query, or make desired_for produce valid work for it"
        ) from error
    if _candidates(capability, (owner_id,)) and not _enrolled(capability, owner_id):
        raise AssertionError(
            f"{name}: discovery selects a database-legal owner with {edge.id} ({edge.value!r}) and desired_for "
            f"declines it. The candidate query says work is owed and desired_for says none is, so the row is "
            f"re-read every tick and holds a page slot a valid owner needs. Exclude it in the candidate query"
        )


def assert_inadmissible_candidate_does_not_block_the_next(
    capability: OwnedDurableWorkCapability[Any, Any, Any], make_implied: Callable[[], Any], edge: CandidateEdge
) -> None:
    """INVARIANT: an edge row first in candidate order does not stop the valid owner behind it."""
    name = capability.model._meta.label_lower
    page_size = _discovery_of(capability).page_size
    assert page_size >= 2, f"{name}: one tick cannot reach a second candidate with page_size={page_size}"
    pair = (make_implied(), make_implied())
    order = _candidates(capability, pair)
    assert sorted(order) == sorted(pair), f"{name}: both implied owners must be discovery candidates"
    head, behind = order
    write_unvalidated(capability.owner_model, head, edge)
    # Excluded, rejected by the database, or sorted after the valid owner, the
    # edge row cannot precede it; the tick still runs and must still enroll it.
    ahead = _candidates(capability, pair) == [head, behind]
    error: Exception | None = None
    try:
        capability.derive()
    except Exception as raised:
        error = raised
    assert _enrolled(capability, behind), (
        f"{name}: a database-legal owner with {edge.id} ({edge.value!r}) "
        f"{'first in candidate order' if ahead else 'in the same tick'} stopped discovery of the valid owner "
        f"behind it (derive() raised {error!r}). One such row then blocks every later owner of its kind on "
        f"every tick"
    )


def discovery_completeness(
    capability: OwnedDurableWorkCapability[Any, Any, Any], make_implied: Callable[[], Any]
) -> CompletenessBinding:
    """
    The discovery page as selection completeness reads it: an owner it skips must owe no work.

    The selection is the production candidate query; the worker is the step
    ``derive()`` runs per owner, ``_derive_owner``, which enrolls what
    ``desired_for`` asks for under the owner lock. A skipped owner that step
    would enroll is owed work no tick discovers.

    Only a discovery declared ``unbounded_because`` claims that: its candidate
    query is every owner still owed. One with a ``horizon`` declares it adopts
    part of what ``desired_for`` admits (social publication adopts only the
    legacy sweep's last day, and leaves retried posts to the compatibility
    path), so the suite does not generate this case for it.
    """
    discovery = _discovery_of(capability)
    owners = capability.owner_model._base_manager

    def candidates() -> Any:
        # REAL PRODUCTION: the candidate query derive() pages through; unbounded, so it is the whole page source.
        return discovery.candidates()

    def make_owner() -> Any:
        # ARRANGE: the derivation example's valid implied owner, as a row.
        return owners.get(pk=make_implied())

    return CompletenessBinding(
        name=f"{capability.model._meta.label_lower} discovery",
        selection=candidates,
        make_owed=make_owner,
        # REAL PRODUCTION: the per-owner step of the discovery phase, on the owner id the page holds.
        execute=partial(capability._derive_owner, discovery),
    )
