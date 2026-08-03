# EVA Documentation Index

This directory contains three kinds of documents. Their status matters:

- **Canonical** documents describe the code that runs today.
- **Experimental** documents describe the optional MVSC pipeline under `packages/`.
- **Research** documents preserve design theory and long-term direction; they are not implementation contracts.

When documents disagree, use this precedence order:

```text
code + tests + OpenAPI
> canonical documentation
> experimental documentation
> research documents
```

## Canonical

| Document | Purpose |
| --- | --- |
| [Architecture](architecture.md) | Current boundaries, runtime topology, data/control flow, and known gaps |
| [Development Guide](../DEVELOPMENT.md) | Local setup, quality gates, and extension workflows |
| [API Reference](api-reference.md) | Stable endpoint families and chat/WebSocket contracts |
| [Development Plan v2](development-plan-v2.md) | Prioritized implementation plan with acceptance criteria |
| [Production Runbook](production-runbook.md) | Deployment, security, monitoring, backup, and recovery |
| [Architecture Boundaries](architecture-boundaries.md) | Import rules enforced by architecture tests |

## Experimental

The following documents apply only when `EVA_ENABLE_MVSC_PIPELINE=true`. The
stable runtime must not import `packages/*` except through `app/experimental.py`.

- [MVSC Architecture](mvsc-architecture.md)
- [MVSC Integration Guide](mvsc-integration-guide.md)
- [MVSC Development Plan](mvsc-dev-plan.md)
- [MVSC Feasibility Analysis](mvsc-feasibility-analysis.md)
- [MVSC Panorama Analysis](eva-mvsc-panorama-analysis.md)

## Research And History

These documents explain the conceptual model behind EVA. Treat them as design
inputs, not promises that every described component is implemented.

- [EVA-VM Architecture](eva-vm-architecture.md)
- [Information Dynamics Consciousness Model](consciousness_model.md)
- [Legacy Operations Runbook](runbook.md)

## Documentation Rules

1. New runtime behavior must update the canonical architecture or API document.
2. Future work belongs in `development-plan-v2.md`, not in the current-state section.
3. Generated OpenAPI at `/openapi.json` is the endpoint schema authority.
4. Secrets, personal paths, and access tokens must never appear in documentation.
