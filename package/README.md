# Due library

Durable obligations for Django and PostgreSQL. This directory is a standalone installable package and the editable subtree used by adopting applications.

- [Capabilities and guarantees](docs/capabilities.md)
- [Application integration](docs/integration.md)
- [Architecture](docs/architecture.md)
- [Conformance API](docs/conformance-api.md)
- [Operations and recovery](docs/operations.md)
- [Migration guides](docs/migrations.md)
- [Runnable public applications](https://github.com/gigaverse-app/due#readme)

Library source, reusable controls and maintenance docs travel together. Application demos, browser/worker integration tests and repository tooling stay in the [full public repository](https://github.com/gigaverse-app/due).

[Local development, package tests and bidirectional subtree sync](docs/development.md).

## Use the projects together

[![Durable runtime: Due](https://img.shields.io/badge/durable_runtime-Due-0F766E)](https://github.com/gigaverse-app/due)
[![Recovery proofs: pytest-obligation](https://img.shields.io/badge/recovery_proofs-pytest--obligation-7C3AED)](https://github.com/gigaverse-app/pytest-obligation)
[![Test selection: pytest-selective](https://img.shields.io/badge/test_selection-pytest--selective-2563EB)](https://github.com/gigaverse-app/pytest-selective)

Due implements durable execution and recovery. Bind
[pytest-obligation](https://github.com/gigaverse-app/pytest-obligation) to your
real workflow to exercise failures and assess its recovery guarantees. Use
[pytest-selective](https://github.com/gigaverse-app/pytest-selective) to plan
affected tests before application startup. For conformance suites, select the
complete lane so its collection and evidence checks remain intact; retain
full-suite checkpoints.

The projects are independently useful and require explicit application binding
and dependency configuration. See the
[conformance guide](https://github.com/gigaverse-app/due/blob/main/package/docs/conformance.md)
and [whole-lane action](https://github.com/gigaverse-app/pytest-selective/tree/main/.github/actions/plan-lanes).
