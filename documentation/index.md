---
title: Due documentation
description: Recover Django background work after crashes with durable work tables and explicit completion evidence.
---

# Due

Recover Django background work after crashes with durable work tables and explicit completion evidence.

[Get started](../package/docs/integration.md){ .md-button .md-button--primary }
[Source on GitHub](https://github.com/gigaverse-app/due){ .md-button }

## Recover work across process failures

Use Due when a Django application must recover outstanding work after a worker
crash or a lost notification. Start with the runtime integration, then define
what counts as successful completion for your application.

- [Wire Django and Celery](../package/docs/integration.md): configure the runtime and stable work identities.
- [Follow a recovery history](../docs/recovery-walkthrough.md): see what survives message loss and process death.
- [Understand guarantees and limits](../package/docs/architecture.md): distinguish a completed attempt from a settled obligation.
- [Test your application](../package/docs/conformance.md): declare and verify the guarantees your integration actually supports.
- [Operate the system](../package/docs/operations.md): inspect failures, recover work, and monitor progress.
- [Compare alternatives](../docs/alternatives.md): decide whether this work-table model fits your workload.

## Use the tools together

[Choose the tool for your problem](ecosystem.md). Each project works on its own;
combine them when you need both faster feedback and evidence that the required
work still happened.

## Versions and support

`latest` follows the main branch. Release-specific documentation is available
through Read the Docs when that release contains the site configuration.
Use [GitHub issues](https://github.com/gigaverse-app/due/issues) for a minimal
reproducer or a missing explanation. [Contributions](../CONTRIBUTING.md) are welcome.
