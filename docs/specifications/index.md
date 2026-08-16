# Requirements

What this service must do, stated so that a test can name it.

These were **distilled from the code, not written before it** — see
[ADR-0001](../decisions/ADR-0001-requirements-are-read-out-of-the-code.md). Every one is
something `flexibility-api` does today and something a reader would want to stay true;
none is an aspiration.

**All fifty-four are satisfied.** Two were not when they were first written — writing the
sentence out is what exposed the defect in each case — and both were fixed the same week:

| | | |
|---|---|---|
| REQ-0011 | `AccessPolicy` never queried the bundle; every decision was a fail-open allow | [#21](https://github.com/celine-eu/flexibility-api/issues/21) |
| REQ-0012 | the Rego raised a rule conflict on the plainest denial there is | [#22](https://github.com/celine-eu/flexibility-api/issues/22) |

REQ-0054 was added with the fix: that the bundle decides correctly and that a request
*gets* that decision are two different claims, and only the first had ever been true.

## How a requirement is verified

A test declares what it covers with a `@verifies REQ-####` tag in its docstring:

```python
async def test_a_participant_may_not_touch_another_participants_commitment(engine):
    """@verifies REQ-0008"""
```

The mapping is a projection of the two and is never written by hand. `.agents/harness.toml`
names no traceability provider, so until the harness checker is available in this
checkout the projection is a grep — `--include='*.py'` because `__pycache__` matches
otherwise:

```bash
grep -rho --include='*.py' "@verifies REQ-[0-9]\{4\}" tests/ | sort | uniq -c
```

CI checks it **both ways** on every push: a requirement no test declares is unverified,
and a tag naming a requirement that does not exist is a typo — and a typo in a trace tag
is indistinguishable from coverage until someone reads the matrix. See
`.github/workflows/test.yaml`.

Adding a requirement means adding a `REQ-####` here **and** a test declaring it, in the
same change.

## The requirements

| | |
|---|---|
| REQ-0001 – REQ-0004 | [identity](identity.md) — who the caller is |
| REQ-0005 – REQ-0014, REQ-0054 | [authorisation](authorisation.md) — what they may do |
| REQ-0015 – REQ-0018 | [windows](windows.md) — how a community surplus becomes an offer |
| REQ-0019 – REQ-0028 | [commitments](commitments.md) — the record of a decision |
| REQ-0029 – REQ-0037 | [suggestions](suggestions.md) — what a participant is shown, and accepting |
| REQ-0038 – REQ-0043 | [settlement](settlement.md) — what the commitment was finally worth |
| REQ-0044 – REQ-0050 | [notifications](notifications.md) — reminders and opportunity nudges |
| REQ-0051 – REQ-0053 | [operability](operability.md) — the MQTT entry point, and degrading |

## What is not here

- **Why** a choice was made — [`docs/decisions/`](../decisions/index.md).
- What the system *is* — [`docs/architecture.md`](../architecture.md).
- A trap that is true of the code and not obvious from it — `.agents/knowledge/`.
- Anything broken — the issue tracker.
