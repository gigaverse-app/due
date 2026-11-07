"""Pure claim fencing and callback admission used by the Django coordinator.

The adapter supplies exact scalar values from freshly locked rows. Integers
and UUID primary keys have different kind tags; UUID tokens are their 128-bit
integer values. Model identity and row presence are explicit adapter facts.
No lease, SQL, transaction or external effect is abstracted as a theorem here.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ClaimAuthority:
    identity_kind: int
    identity: int
    state: str
    token: int
    revision: int


# @ ensures result == (same_model and captured.identity_kind == current.identity_kind and captured.identity == current.identity and captured.state == current.state and captured.token == current.token and captured.revision == current.revision)
def claim_authorized(same_model: bool, captured: ClaimAuthority, current: ClaimAuthority) -> bool:
    """Every captured fence fact must match before admitting a callback."""
    return (
        same_model
        and captured.identity_kind == current.identity_kind
        and captured.identity == current.identity
        and captured.state == current.state
        and captured.token == current.token
        and captured.revision == current.revision
    )


# @ requires len(captured) == len(current) and len(captured) == len(same_models) and len(captured) == len(present)
# @ ensures len(result) == len(captured)
# @ ensures forall i in range(len(result)) :: result[i] == (present[i] and same_models[i] and captured[i].identity_kind == current[i].identity_kind and captured[i].identity == current[i].identity and captured[i].state == current[i].state and captured[i].token == current[i].token and captured[i].revision == current[i].revision)
def callback_admissions(
    captured: list[ClaimAuthority],
    current: list[ClaimAuthority],
    same_models: list[bool],
    present: list[bool],
) -> list[bool]:
    """Build the ordered admission decisions before any batch callback runs.

    One slot per captured completion preserves order and duplicate-row rejection.
    The caller removes a locked row after its first occurrence, making subsequent
    occurrences absent. All prepare and finalizer callbacks consume this plan.
    """
    admitted: list[bool] = []
    for i in range(len(captured)):
        # @ invariant len(admitted) == i
        # @ invariant forall j in range(i) :: admitted[j] == (present[j] and same_models[j] and captured[j].identity_kind == current[j].identity_kind and captured[j].identity == current[j].identity and captured[j].state == current[j].state and captured[j].token == current[j].token and captured[j].revision == current[j].revision)
        authorized = claim_authorized(same_models[i], captured[i], current[i])
        admitted.append(present[i] and authorized)
    return admitted
