# ADR-0001 — the requirements are read out of the code, not written before it

**Date:** 2026-08-15
**Status:** accepted

## Context

This service had fifteen tests, all in one module, all covering the construction of
flexibility suggestions. Settlement, points, commitments, the MQTT listener and the
authorisation policy had none. It also had no stated requirements: nothing anywhere said
what a settlement was supposed to compute.

Suggestion building — the one covered area — is the part where a defect is most visible,
because a bad suggestion looks wrong on the screen. Settlement is the opposite. A wrong
settlement is a plausible number, and nobody is positioned to notice it.

Closing the test gap needs the requirement gap closed first: a suite with nothing to trace
to measures only itself.

The honest options were two.

**Write the requirements first, from intent.** Ask what this service is *for*, state it,
then test against it. This produces a specification genuinely independent of the
implementation — and a first run in which most tests fail, with no way to tell a defect
from a requirement nobody ever agreed to.

**Read the requirements out of the code.** State what the service does today, in language
that says why it matters, and pin it. This produces a specification that cannot find a
defect by construction — it agrees with the code — but that does make every subsequent
change visible, because a change now contradicts a written sentence.

`../celine-grid` and `../celine-ai-assistant` both faced this and took the second.

The plan that preceded this work (`.agents/plans/the-consequential-half-is-untested.md`)
held two questions open for the operator: whether settlement had a specification anywhere,
and whether the points formula was a product decision. Neither had an answer. Testing a
money-adjacent calculation against the code that computes it pins the current behaviour,
right or wrong — which is what was chosen, with the arithmetic written out in prose so
that the next reader can disagree with it.

## Decision

Distil the requirements from the code. Each `REQ-####` states a behaviour the service has
today and that a reader would want to stay true. Each is verified by at least one test
declaring it with `@verifies REQ-####`.

Where reading the code turned up behaviour that is *wrong* rather than merely
undocumented, state the **correct** behaviour as the requirement, file an issue, and mark
its test `xfail(strict=True)` with a characterisation test beside it pinning what happens
now. REQ-0011 and REQ-0012 are the two such cases.

Where a behaviour is merely surprising — the banker's rounding in REQ-0039, the `total`
that counts a page in REQ-0023, the `403`-instead-of-`401` in REQ-0014 — state it plainly
and say it was not corrected. A surprise written down is a decision; a surprise left
implicit is an accident waiting to be reproduced.

## Consequences

The suite could not have found a defect it was not looking for, and mostly did not. The
two it did find are both authorisation, and both surfaced from *writing the sentence out*
rather than from a failing assertion: stating "a denial must reach the caller" made it
obvious that no denial ever had. That is the yield to expect from this method, and it is
why the prose matters more than the assertion beneath it.

A requirement distilled this way is worth less than one agreed in advance: it cannot
contradict the implementation, so it cannot catch the case where the implementation was
never what anyone wanted. **Settlement is exactly that case** — REQ-0039 pins
`round(sum(kwh) × 10)` because that is what runs, not because anyone confirmed it is the
intended reward. If the intended rule is written down somewhere — a regulation, a design
note, a spreadsheet — the requirement should be rewritten against it and the difference
is the finding.

Someone will eventually want to change a behaviour and find a `REQ-####` in the way. The
requirement is not an authority — it is a record of what was true on 2026-08-15. Change it
and its test together, in the same commit, and the record stays honest.
