# Extraction boundary

Due's initial implementation is derived from Gigaverse's durable-work work-table
implementation at source revision `04786fb2d`. The extraction preserves the
coordination, producer, evidence, projection, scheduled-action, compensation and
recovery mechanisms. It does not bring over application adopters or backend history.

The original library package becomes `due.django`. Pure rejection/completion
contracts, observations and retry arithmetic live outside the Django namespace.
Application tenant context and read-after-write routing become explicit hooks.
Metrics use `due_` names to avoid registry collisions during consumer migration.
Due owns migrations for its two concrete infrastructure tables; applications own
their concrete work tables. Apache-2.0 is the public license.

Native-table composition (`CompletionLease`, `RecoveryRoute` and a public native
`RecoveryLease`) is deliberately excluded. An internal observation delivery helper
retains the shared arbitration needed by work-table observation workers.

Backend-specific test discovery, provider fixtures, alerts, old task aliases and
repository-specific conformance generators are not shipped. Regression tests are
ported to isolated rows and standalone settings. The public `pytest-obligation`
package supplies the neutral generated proofs; work-table bindings delegate to Due.

Consumer adoption is a separate data/import/worker-route cutover; see
[integration](integration.md). This release has no framework-neutral persistence
adapter and does not publish or migrate any backend application data.
