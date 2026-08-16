# ADR-0002 — the OPA bundle is real in tests, and the suite refuses to run without it

**Date:** 2026-08-15
**Status:** accepted

## Context

`AccessPolicy._evaluate` returns `Decision(True, …)` in two failure cases: the bundle did
not load, and the evaluation raised. Both are development conveniences, and both are
indistinguishable — from the response alone — from a policy that ran and permitted.

That has a consequence for testing which is easy to miss. **A suite that faked the policy
engine and a suite whose engine silently failed to load would produce identical passes.**
Every `allow` assertion would be green in both. The fake would be measuring itself.

`celine.sdk.policies` evaluates Rego in process, through `regorus` — no OPA server, no
socket, no container. So the real bundle costs nothing to run.

`../celine-grid` reached the same conclusion in the same week, for the same fail-open
reason, and its ADR-0002 says so.

## Decision

Evaluate `policies/flexibility.rego` for real. Fake nothing about authorisation.

Assert **denials**, not allows. An allow is produced by the working path and by both
broken ones; only a refusal proves the bundle was consulted at all.

Guard it with a `policy_engine` fixture that asserts `AccessPolicy()._engine is not None`
and that the package is registered. A missing bundle is then a fixture error rather than
a suite that passes while proving nothing.

## Consequences

The suite found both authorisation defects — [#21](https://github.com/celine-eu/flexibility-api/issues/21)
and [#22](https://github.com/celine-eu/flexibility-api/issues/22), both since fixed — and
could not have found either with a faked engine. #21 in particular is only visible because
the query the wrapper builds was actually handed to `regorus`, which rejected it.

#22 is the sharper argument for this decision. A rule conflict is a **runtime** error in
Rego: the bundle parses, loads, and reports its package, and only the input that reaches
two matching rules raises. Nothing short of evaluating real inputs against the real bundle
would have found it — not a linter, not a load check, and certainly not a fake. CI now
walks the denial branches for the same reason.

The tests that query the bundle bypass `AccessPolicy` and call
`PolicyEngine.evaluate("data.celine.flexibility.access", …)` directly. That is a
deliberate second seam and it has a cost: on its own it verifies the bundle and the
wrapper, but not *the path an HTTP request actually takes through them*.

REQ-0054 closes that with an end-to-end test through `client` — an unscoped service token
refused by a real request, carrying the bundle's own reason string out to the response
body. It could not be written until #21 was fixed, because until then every decision was
an allow. **That is the shape to keep: a denial, asserted on its reason, through the
app.** A reason travelling the whole way is what distinguishes "the policy refused this"
from "something else did".

The input document is assembled by hand in the tests rather than taken from
`AccessPolicy.allow_user_commitment`, because that method is unreachable — nothing calls
it. Borrowing its shape would tie the tests to code no request executes. If a route starts
calling it, the tests should switch to it.
