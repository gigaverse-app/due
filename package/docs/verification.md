# What Due's tests establish

Always build the application contract using the [conformance guide](conformance.md).
Do it alongside the first capability so its generated failure tests become part
of normal development. A passing Due library suite checks Due's implementation against its
fixtures and examples; your provider, transaction boundaries and recovery paths
still need application-owned contracts.

## Read the evidence at the right boundary

| Evidence | What it establishes | What you still need to check |
| --- | --- | --- |
| PostgreSQL regression tests | Atomic admission, ownership fencing, recovery and settlement for the tested histories | Your models, migrations, queries and transaction boundaries |
| Generated conformance reports | The declared guarantees whose required cases actually executed and passed | Every application capability is enrolled and required cases were not filtered out |
| Celery/Redis worker journeys | The example recovers through real worker processes and broker delivery | Your task routes, schedules, worker settings and deployment configuration |
| Installed-wheel checks | The built distribution imports and contains its runtime resources | Compatibility with your application's locked environment |

A successful dispatch is not proof of the business result. Observe product state
and provider outcomes independently, including the intended successors and
cleanup. Inject faults at external boundaries while keeping production admission,
execution and recovery real.

## Verify your application

1. Bind the [capability](capabilities.md) to real application operations and state.
2. Follow [conformance authoring](conformance.md), including independent expected
   outcomes, required examples and controls that demonstrate each fault occurred.
3. Retain the profile, application-suite and observation reports from that run.
   Reject missing or filtered required cases; inspect unassessed and expected
   failures separately from passing guarantees.
4. Exercise the deployed worker and recurring recovery configuration described
   in [operations](operations.md), in addition to deterministic fault histories.

For library development, use the commands in
[CONTRIBUTING.md](../../CONTRIBUTING.md) and inspect the
[current CI runs](https://github.com/gigaverse-app/due/actions/workflows/ci.yml).
Historical test counts are not a statement about the current release.

## Limits

An application binding cannot infer a remote provider's idempotency promise.
Local controlled histories do not certify every remote integration, prove
throughput, or establish that an application's data migration has completed.
Read the [architecture and guarantees](architecture.md) alongside your executed
reports before treating a green result as a production guarantee.
