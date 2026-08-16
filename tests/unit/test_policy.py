"""The authorisation rules, evaluated against the real `policies/flexibility.rego`.

These are the tests the suite exists for. `AccessPolicy` returns `Decision(True, …)`
whenever the engine is missing or the evaluation raises, so an *allow* proves nothing on
its own — **a denial is the only observation that distinguishes a policy that ran from
one that was never consulted.** The `policy_engine` fixture refuses to let these tests
run against an unloaded bundle.

The tests split into two halves, and the split is the point:

- **The bundle.** What `flexibility.rego` decides, queried directly.
- **The wiring.** What `AccessPolicy` does with the bundle — that it carries the
  decision out, and that the two fail-open branches are still reachable and still
  labelled.

Both halves were broken until 2026-08-15 (#21, #22): the wrapper built a malformed query
so the bundle was never consulted, and the bundle raised a rule conflict on the plainest
denial there is. `tests/api/test_commitments.py` carries the end-to-end counterpart, which
could not exist while either was true.
"""

from __future__ import annotations

import pytest

from celine.flexibility.security.policy import AccessPolicy

PACKAGE = "data.celine.flexibility.access"
OWNER = "user-alice"
STRANGER = "user-bob"


# ---------------------------------------------------------------------------
# Querying the bundle
# ---------------------------------------------------------------------------


def _input(
    *,
    subject_id: str,
    action: str = "read",
    owner_id: str | None = OWNER,
    is_service: bool = False,
    scopes: list[str] | None = None,
) -> dict:
    """The input document `AccessPolicy` builds, assembled by hand.

    Built here rather than taken from `AccessPolicy.allow_user_commitment` on purpose:
    that method is unreachable — nothing calls it — so borrowing its shape would tie
    these tests to code no request ever executes.
    """
    resource: dict = {"type": "flexibility.commitment"}
    if owner_id is not None:
        resource["attributes"] = {"owner_id": owner_id}
    return {
        "action": {"name": action},
        "resource": resource,
        "subject": {
            "id": subject_id,
            "is_service": is_service,
            "scopes": scopes if scopes is not None else [],
            "groups": [],
        },
    }


def decide(engine, input_data: dict) -> dict:
    """Evaluate the whole package and return its document.

    `data.<package>` rather than `data.<package>.allow` because the reason is half of
    what is being asserted — a denial with the wrong reason is a denial nobody can act
    on.
    """
    result = engine.evaluate(PACKAGE, input_data)
    return result["result"][0]["expressions"][0]["value"]


@pytest.fixture
def engine(policy_engine):
    return policy_engine


# ---------------------------------------------------------------------------
# The bundle — a participant
# ---------------------------------------------------------------------------


# @verifies REQ-0006
def test_the_owner_of_a_commitment_may_read_it(engine):
    out = decide(engine, _input(subject_id=OWNER, scopes=["flexibility.read"]))

    assert out["allow"] is True
    assert out["reason"] == "user accessing own commitment"


@pytest.mark.parametrize("action", ["read", "write", "delete"])
# @verifies REQ-0006
def test_the_owner_may_read_write_and_delete(engine, action):
    """
    One scope covers all three actions — `has_any_scope` is checked, not a per-action
    scope. A participant holding only `flexibility.read` may therefore *delete* their
    own commitment, which is what `DELETE /api/commitments/{id}` relies on.
    """
    out = decide(
        engine, _input(subject_id=OWNER, action=action, scopes=["flexibility.read"])
    )

    assert out["allow"] is True


# @verifies REQ-0008
def test_a_participant_may_not_touch_another_participants_commitment(engine):
    """
    The most consequential rule here. A commitment carries settlement consequences and a
    points balance, and there is no shape difference between one participant's row and
    another's — so this comparison is the whole of the separation between two members of
    the same community.
    """
    out = decide(engine, _input(subject_id=STRANGER, scopes=["flexibility.read"]))

    assert out["allow"] is False
    assert out["reason"] == "not resource owner"


# @verifies REQ-0007
def test_owning_the_commitment_is_not_enough_without_a_flexibility_scope(engine):
    out = decide(engine, _input(subject_id=OWNER, scopes=[]))

    assert out["allow"] is False
    assert out["reason"] == "missing flexibility scope"


@pytest.mark.parametrize(
    "scope", ["flexibility.read", "flexibility.write", "flexibility.admin"]
)
# @verifies REQ-0007
def test_any_of_the_three_flexibility_scopes_admits_the_owner(engine, scope):
    assert decide(engine, _input(subject_id=OWNER, scopes=[scope]))["allow"] is True


# @verifies REQ-0007
def test_an_unrelated_scope_does_not_admit(engine):
    """
    An absent `scope` claim and a `scope` claim naming something else land on the same
    denial.
    """
    out = decide(engine, _input(subject_id=OWNER, scopes=["grid.read", "openid"]))

    assert out["allow"] is False
    assert out["reason"] == "missing flexibility scope"


# ---------------------------------------------------------------------------
# The bundle — a service account
# ---------------------------------------------------------------------------


# @verifies REQ-0009
def test_a_service_account_reads_on_scope_and_owns_nothing(engine):
    """
    A service account holds no `owner_id` to match, so ownership is never consulted for
    it — the scope is the whole check. `settle` and `pending` run under exactly this
    branch.
    """
    out = decide(
        engine,
        _input(
            subject_id="svc-flexibility",
            action="service",
            owner_id=None,
            is_service=True,
            scopes=["flexibility.read"],
        ),
    )

    assert out["allow"] is True
    assert out["reason"] == "service access granted"


# @verifies REQ-0009
def test_a_service_account_without_a_flexibility_scope_is_refused(engine):
    out = decide(
        engine,
        _input(
            subject_id="svc-flexibility",
            action="service",
            owner_id=None,
            is_service=True,
            scopes=[],
        ),
    )

    assert out["allow"] is False
    assert out["reason"] == "missing flexibility scope"


# @verifies REQ-0009
def test_a_service_write_needs_write_or_admin_not_read(engine):
    """
    `flexibility.read` — enough to read every participant's commitments — is not enough
    to change one.
    """
    read_only = _input(
        subject_id="svc-flexibility",
        action="write",
        owner_id=None,
        is_service=True,
        scopes=["flexibility.read"],
    )
    writer = dict(read_only, subject=dict(read_only["subject"], scopes=["flexibility.write"]))

    assert decide(engine, read_only)["allow"] is False
    assert decide(engine, writer)["allow"] is True


# @verifies REQ-0010
def test_export_requires_its_own_scope(engine):
    """
    `GET /api/commitments/export` returns every commitment of every participant in every
    status. `flexibility.admin` does not reach it and neither does `flexibility.read`:
    the export scope is separate precisely because the blast radius is.
    """
    def export_with(scopes: list[str]) -> bool:
        return decide(
            engine,
            _input(
                subject_id="svc-mirror",
                action="export",
                owner_id=None,
                is_service=True,
                scopes=scopes,
            ),
        )["allow"]

    assert export_with(["flexibility.commitments.export"]) is True
    assert export_with(["flexibility.admin"]) is False
    assert export_with(["flexibility.read"]) is False


# ---------------------------------------------------------------------------
# The bundle — failing closed
# ---------------------------------------------------------------------------


# @verifies REQ-0005
def test_an_action_the_policy_does_not_name_is_refused(engine):
    """
    `default allow := false`. A route that grows a new action and no rule for it fails
    closed — which is the opposite of what the Python wrapper around this bundle does.
    """
    out = decide(engine, _input(subject_id=OWNER, action="frobnicate", scopes=["flexibility.admin"]))

    assert out["allow"] is False
    assert out["reason"] == "access denied"


# @verifies REQ-0012
def test_a_stranger_without_scope_is_denied_with_one_reason(engine):
    """
    The plainest denial there is: the caller owns nothing and is scoped for nothing.

    It used to be the one input that **raised** — it satisfied two of the three denial
    rules, and Rego refuses to let a complete rule produce two values — so the fail-open
    wrapper turned the weakest claim in the system into an allow (#22). The rules are now
    one `else` chain, which makes precedence explicit rather than relying on the bodies
    being disjoint.

    Ownership wins over scope in that ordering, which is the more fundamental failure of
    the two.
    """
    out = decide(engine, _input(subject_id=STRANGER, scopes=[]))

    assert out["allow"] is False
    assert out["reason"] == "not resource owner"


@pytest.mark.parametrize(
    ("case", "subject", "action", "is_service", "scopes", "expected"),
    [
        ("owner, scoped",        OWNER,    "read",       False, ["flexibility.read"],  "user accessing own commitment"),
        ("stranger, scoped",     STRANGER, "read",       False, ["flexibility.read"],  "not resource owner"),
        ("stranger, unscoped",   STRANGER, "read",       False, [],                    "not resource owner"),
        ("owner, unscoped",      OWNER,    "read",       False, [],                    "missing flexibility scope"),
        ("user on a svc route",  OWNER,    "service",    False, ["flexibility.read"],  "service account required"),
        ("stranger on svc route", STRANGER, "service",   False, [],                    "service account required"),
        ("service, scoped",      "svc",    "service",    True,  ["flexibility.read"],  "service access granted"),
        ("service, unscoped",    "svc",    "service",    True,  [],                    "missing flexibility scope"),
        ("service, wrong scope", "svc",    "write",      True,  ["flexibility.read"],  "access denied"),
        ("unknown action",       OWNER,    "frobnicate", False, ["flexibility.admin"], "access denied"),
    ],
    ids=lambda v: v if isinstance(v, str) and " " in v else None,
)
# @verifies REQ-0012
def test_every_reason_is_single_valued(engine, case, subject, action, is_service, scopes, expected):
    """
    The `else` chain is only correct if it is *total* and *ordered*. This walks every
    branch of it, including the three combinations that used to collide, and asserts the
    exact string — because the reason is what a client is shown and what an operator
    greps for.

    A conflict anywhere would raise rather than return, so this also asserts that none of
    these inputs can reach the fail-open path.

    The resource is always owned by `OWNER`; `subject` is who is asking. A service
    subject is given no owner to match, because it never has one.
    """
    out = decide(
        engine,
        _input(
            subject_id=subject,
            action=action,
            owner_id=None if is_service else OWNER,
            is_service=is_service,
            scopes=scopes,
        ),
    )

    assert out["reason"] == expected


# ---------------------------------------------------------------------------
# The wiring — what AccessPolicy does with the bundle
# ---------------------------------------------------------------------------


# @verifies REQ-0011
def test_the_bundle_loads_from_an_absolute_path(policy_engine):
    """
    `_POLICIES_DIR` is derived from `__file__`, not from the working directory, so the
    bundle is found wherever pytest was invoked from. A relative path here would make
    the whole service fail open when started from anywhere but the repository root — and
    it would do it silently.
    """
    assert policy_engine.has_package("celine.flexibility.access")


# @verifies REQ-0011
async def test_a_denied_subject_is_denied_by_the_policy_object(policy_engine):
    """
    The decision the bundle made has to reach the caller. It did not until #21: the query
    was built by string-joining the package path with slashes and handed to
    `PolicyEngine.evaluate()`, which evaluates its argument as **Rego** — so every call
    raised and every raise became an allow.

    This is the assertion that would have caught it, and it is a *denial* for the reason
    the module docstring gives: an allow is produced by the working path and by both
    broken ones.
    """
    policy = AccessPolicy()

    decision = await policy._evaluate(_input(subject_id=STRANGER, scopes=["flexibility.read"]))

    assert decision.allowed is False
    assert decision.reason == "not resource owner"


# @verifies REQ-0011
async def test_an_allowed_subject_is_allowed_by_the_policy_object(policy_engine):
    """
    The other direction, which on its own proves nothing — it is here so that a change
    breaking allows is not mistaken for the policy simply being strict.
    """
    policy = AccessPolicy()

    decision = await policy._evaluate(_input(subject_id=OWNER, scopes=["flexibility.read"]))

    assert decision.allowed is True
    assert decision.reason == "user accessing own commitment"


# @verifies REQ-0011
async def test_a_missing_engine_allows_everything():
    """
    Fail-open, still deliberate and still reachable — it was not removed, because whether
    authorisation should take the service down when its bundle is bad is a decision
    nobody has been asked to make.

    What changed is that this branch is no longer taken on every call. The reason string
    is the only signal, which is why it is asserted here: it is what a deployment
    intolerant of fail-open has to alert on.
    """
    policy = AccessPolicy()
    policy._engine = None

    decision = await policy._evaluate({})

    assert decision.allowed is True
    assert decision.reason == "no-policy-engine"


# @verifies REQ-0011
async def test_an_evaluation_that_raises_allows(policy_engine, monkeypatch):
    """
    The second fail-open branch. Reached now only by a genuine engine failure — a bundle
    that parses but whose `allow` rule raises — rather than by every request.
    """
    policy = AccessPolicy()

    def _explode(query, input_data):
        raise RuntimeError("regorus fell over")

    monkeypatch.setattr(policy._engine, "evaluate", _explode)

    decision = await policy._evaluate(_input(subject_id=STRANGER, scopes=[]))

    assert decision.allowed is True
    assert decision.reason == "policy-error-permissive"


# @verifies REQ-0011
async def test_an_unreadable_reason_does_not_change_the_decision(policy_engine, monkeypatch):
    """
    `allow` and `reason` are queried separately, and only the first can fail open. #22 is
    the argument for that split: a conflict among the *reason* rules used to raise, and
    the raise took the whole decision with it — so a broken message flipped a denial into
    an allow.

    Now a reason that cannot be read costs the message and nothing else.
    """
    policy = AccessPolicy()
    real = policy._engine.evaluate

    def _reason_explodes(query, input_data):
        if query.endswith(".reason"):
            raise RuntimeError("rule conflict")
        return real(query, input_data)

    monkeypatch.setattr(policy._engine, "evaluate", _reason_explodes)

    decision = await policy._evaluate(_input(subject_id=STRANGER, scopes=[]))

    assert decision.allowed is False
    assert decision.reason is None
