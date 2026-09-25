---
name: wf-spec
description: >-
  Turn an approved PRD into a reviewable, implementation-free architecture and contract spec. Use before the roadmap for new projects or major initiatives. Triggers: "wf spec", "architecture spec", "design the project".
---

# wf-spec

Read `references/project.md`, `references/engineering.md`, `references/security.md`, and
`templates/spec.md`. Require an approved `development/project/prd.md`. Inspect the existing project
structure and declared constraints before proposing a design.

Write `development/project/spec.md` with the simplest architecture that meets the PRD. Define the
feature boundaries, entry points, data ownership, public interfaces and contracts needed for independent
tasks, security boundaries, quality checks, deployment assumptions if relevant, and test strategy.
Choose a stack only where the human has not already chosen one; explain material tradeoffs. Use diagrams
only when they clarify a boundary. Keep later details flexible until the related milestone is planned.
Name the tracked document (for example `docs/architecture.md`) that will carry the decisions others need
(`references/project.md` → Private state).

Before the gate, run the review loop in `references/project.md` → Spec and roadmap review (read
`references/loop.md` for running a fresh stage, and `references/models.md` for non-default model or
effort choices). If a setting is unsupported or a review fails, stop without an approval verdict.
Show the human the decisions, contracts, risks, and review result. Explicit approval records their
words and date; feedback returns it to Draft. Next: `wf roadmap`. Do not scaffold code.

## Review (`wf spec rev`, run by the review loop)

Work only from the PRD, the spec, and the repository, following `references/review-rubric.md` with
`D<round>-<n>` finding IDs. Round 1 checks PRD coverage, feasibility, security boundaries, contracts
precise enough for independent tasks, and avoidable complexity (`references/engineering.md`). Later
rounds are delta reviews of the latest revision. Do not edit the spec. In delegated output mode, write
nothing and return only the round.
