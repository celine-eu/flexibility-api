# Authorisation

Two mechanisms, and they cover different things.

**`policies/flexibility.rego`**, evaluated in process, is the specification of record for
what a subject may do — and it is reached from exactly one place, `PolicyMiddleware`,
guarding two routes. REQ-0005 to REQ-0010 state what the bundle decides; REQ-0054 states
that a request actually gets that answer.

**Everything else is SQL.** No participant-facing route consults the policy at all. The
separation between Alice's commitments and Bob's is `WHERE user_id = :sub` in
`src/celine/flexibility/api/commitments.py`, and it is specified with the commitments (REQ-0021, REQ-0024).

`AccessPolicy.allow_user_commitment` builds a participant input document and is called by
nothing. It is the shape a future route would use, not a rule in force.

---

### REQ-0005 — the bundle denies by default

`default allow := false`, `default reason := "access denied"`. An action the policy does
not name is refused, so a route that grows a new action and no rule for it fails closed.

The Python wrapper inverts this in two cases, both deliberate and both stated in
REQ-0011.

### REQ-0006 — the owner of a commitment may read, write and delete it

A non-service subject passes when `resource.attributes.owner_id == subject.id` and it
holds any flexibility scope. Reason: `user accessing own commitment`.

One scope covers all three actions — `has_any_scope` is checked, not a per-action scope —
so a participant holding only `flexibility.read` may delete their own commitment. That is
what a cancel needs and it is why the three actions are not separated.

### REQ-0007 — owning it is not enough without a flexibility scope

Any of `flexibility.read`, `flexibility.write`, `flexibility.admin` admits the owner.
None of them, or a scope naming something else, is refused with
`missing flexibility scope`. An absent `scope` claim and a wrong one land on the same
denial.

### REQ-0008 — a participant may not touch another participant's commitment

Refused with `not resource owner`.

This is the most consequential rule in the bundle. A commitment carries settlement
consequences and a points balance, and one participant's row has no shape difference from
another's — so this comparison is the whole of the separation between two members of the
same community.

Note that it protects no route today. The participant-facing routes never reach the
policy; see the SQL requirements instead.

### REQ-0009 — a service account is admitted on scope and owns nothing

A service subject has no `owner_id` to match, so ownership is never consulted for it and
the scope is the whole check:

- `read` and `service` need `flexibility.read` or `flexibility.admin`
- `write` and `delete` need `flexibility.write` or `flexibility.admin`

`flexibility.read` — enough to read every participant's commitments — is not enough to
change one. A service account with no flexibility scope at all is refused with
`missing flexibility scope`.

### REQ-0010 — export has its own scope

`export` needs `flexibility.commitments.export`. Neither `flexibility.admin` nor
`flexibility.read` reaches it.

The scope is separate because the blast radius is: `GET /api/commitments/export` returns
every commitment of every participant in every status, which no other route does.

**The route does not check it.** `PolicyMiddleware` guards only `/pending` and
`PATCH …/settle`, so export is held by its `ServiceDep` alone and any service token
reaches it — see REQ-0028.

### REQ-0011 — a decision the bundle makes reaches the caller

`AccessPolicy._evaluate` queries `data.celine.flexibility.access.allow` and returns what
the bundle decided.

The query is built with **dots**. `PolicyEngine.evaluate()` evaluates its argument as a
Rego query, not as a package name, so the slash-separated path that used to be passed was
not valid Rego at all: `regorus` raised, the `except` returned an allow, and the bundle
was never consulted on any call
([#21](https://github.com/celine-eu/flexibility-api/issues/21), fixed 2026-08-15).

`allow` and `reason` are queried **separately**, and only `allow` can fail open. A reason
is a message; an allow is an authorisation, and REQ-0012 is what a broken message looks
like. A reason that cannot be read now costs the message and nothing else.

The bundle loads from an absolute path derived from `__file__` rather than the working
directory, so the service finds it wherever it is started from.

**Fail-open remains, deliberately**, in two branches that are indistinguishable — from a
response alone — from a policy that ran and permitted:

- the bundle did not load — `Decision(True, "no-policy-engine")`
- the `allow` evaluation raised — `Decision(True, "policy-error-permissive")`

Neither is reached in normal operation any more. Whether authorisation should instead fail
*closed* is an open product decision with a real operational cost — a bad bundle would
take the service down rather than open it — and nobody has been asked. Until then the
reason string is the only signal, and it is what a deployment intolerant of fail-open must
alert on.

**The test suite refuses to run its policy tests unless the bundle loaded**, because
otherwise every assertion about authorisation would pass for the wrong reason.

### REQ-0012 — every denial produces exactly one reason

`reason` is a single rule with an `else` chain, so precedence is explicit and total rather
than resting on the bodies being disjoint. Most specific first: allowed-as-user, allowed-as-service,
`service account required`, `not resource owner`, `missing flexibility scope`, then the
`access denied` default.

It was five separate rules and they were not disjoint
([#22](https://github.com/celine-eu/flexibility-api/issues/22), fixed 2026-08-15). A
subject who is neither the owner nor scoped satisfied two of them, and Rego refuses to let
a complete rule produce two values, so the evaluation raised — and a raise becomes an
allow. **The caller with the weakest claim was the one that got in.**

Ownership takes precedence over scope in the chain: a stranger holding no scope reads
`not resource owner`, because that is the more fundamental of the two failures.

### REQ-0013 — the two service-only routes are refused to a participant

`GET /api/commitments/pending` and `PATCH /api/commitments/{id}/settle` refuse a
participant token with `403 not-a-service-account`.

That refusal comes from the `is_service_account` check in `AccessPolicy.allow_service`,
which returns **before** the bundle is queried. It is a second, independent gate and it is
now redundant with a working policy — REQ-0009 would refuse the same caller. It stays:
deleting a check that worked, on the strength of one that was broken until today, is not
a trade worth making.

`GET /api/commitments/export` is refused to a participant too, by its `ServiceDep` rather
than the middleware — a different mechanism reaching the same status, with a different
detail string (`Service account required`).

### REQ-0054 — a guarded route enforces the scope, not just the account type

A service account holding no flexibility scope is refused on
`GET /api/commitments/pending` and `PATCH /api/commitments/{id}/settle` with
`403 missing flexibility scope` — the bundle's own reason, carried out to the response
body.

This is the requirement that the whole mechanism actually runs, and it is stated
separately from REQ-0009 because the bundle deciding correctly and a request being decided
are two different claims. Until #21 was fixed only the first was true: **any** valid
Keycloak service token, belonging to any client in the realm, reached both routes.

It does **not** extend to `/export`, which the middleware does not match at all — see
REQ-0028.

### REQ-0014 — a missing token on a guarded route is a `403`, not a `401`

`PolicyMiddleware` runs ahead of the dependency chain, catches the exception the token
extraction raises, and returns `403 unauthenticated`.

So the same missing token produces `401` on `/api/commitments` and `403` on
`/api/commitments/pending`. A client cannot tell "present a token" from "your token is
not enough" on the two guarded routes, and a proxy that refreshes on `401` will not
refresh here.

Stated because it is surprising, not because it is right.
