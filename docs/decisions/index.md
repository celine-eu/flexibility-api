# Decisions

Architecture decision records: **why a technical choice was made here**, when the reason
is not derivable from the code and would otherwise be re-litigated.

One file per decision, named `ADR-####-short-slug.md`, with this shape:

```markdown
# ADR-0001 — <the decision, as a statement>

**Date:** <ISO-8601>
**Status:** accepted | superseded by ADR-####

## Context
<what forced a choice. The constraint, and what had already been tried.>

## Decision
<what was decided, in the imperative.>

## Consequences
<what this costs, what it forecloses, and what will tempt someone to undo it.>
```

## What is not an ADR

- **A requirement.** What the product must do belongs with the requirements, where it can
  be traced to a test. An ADR is measured by nothing.
- **A rule with a referent that something already measures.** If a statement could carry
  an identifier and a test that names it, put it where that measurement happens. Deciding
  it here hides it from the report.
- **A procedure.** That is `.agents/playbooks/`.
- **A fact about the code.** That is `.agents/knowledge/`.

An ADR is immutable once accepted. It is superseded by a later ADR that names it, never
edited to say something else.

## The records

| | | |
|---|---|---|
| [ADR-0001](ADR-0001-requirements-are-read-out-of-the-code.md) | the requirements are read out of the code, not written before it | accepted |
| [ADR-0002](ADR-0002-the-policy-bundle-is-real-in-tests.md) | the OPA bundle is real in tests, and the suite refuses to run without it | accepted |
| [ADR-0003](ADR-0003-the-database-is-real-and-sqlite.md) | the database is real in tests, and it is SQLite | accepted |
| [ADR-0004](ADR-0004-every-other-boundary-is-faked-at-the-sdk-client.md) | every other boundary is faked at the SDK client | accepted |
