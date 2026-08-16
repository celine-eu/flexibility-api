# METADATA
# title: Flexibility Commitment Access Policy
# description: Controls read/write access to flexibility commitments
# scope: package
# entrypoint: true
package celine.flexibility.access

import rego.v1

default allow := false
default reason := "access denied"

# ── helpers ──────────────────────────────────────────────────────────────────

is_owner if {
    input.resource.attributes.owner_id == input.subject.id
}

is_service if {
    input.subject.is_service == true
}

has_scope(scope) if {
    scope in input.subject.scopes
}

has_any_scope(scopes) if {
    some s in scopes
    s in input.subject.scopes
}

# ── user: own data ────────────────────────────────────────────────────────────

allow if {
    not is_service
    input.action.name in {"read", "write", "delete"}
    is_owner
    has_any_scope(["flexibility.read", "flexibility.write", "flexibility.admin"])
}

# ── service: full access with appropriate scope ───────────────────────────────

allow if {
    is_service
    input.action.name == "export"
    has_scope("flexibility.commitments.export")
}

allow if {
    is_service
    input.action.name in {"read", "service"}
    has_any_scope(["flexibility.read", "flexibility.admin"])
}

allow if {
    is_service
    input.action.name in {"write", "delete"}
    has_any_scope(["flexibility.write", "flexibility.admin"])
}

# ── the reason ────────────────────────────────────────────────────────────────
#
# One rule with an `else` chain, so precedence is explicit and total.
#
# These were five separate rules and they were not disjoint: a subject who is neither
# the owner nor scoped satisfied two of them, and `reason` is a complete rule, so Rego
# raised `rule conflict` rather than producing a denial. The caller of this bundle turns
# a raised evaluation into an *allow*, which made the plainest denial there is — a
# subject who owns nothing and is scoped for nothing — the one that got through.
#
# Order is most-specific first. Anything reaching the end is the `default` above.

reason := "user accessing own commitment" if {
    allow
    not is_service
} else := "service access granted" if {
    allow
} else := "service account required" if {
    input.action.name == "service"
    not is_service
} else := "not resource owner" if {
    not is_service
    not is_owner
} else := "missing flexibility scope" if {
    not has_any_scope(["flexibility.read", "flexibility.write", "flexibility.admin"])
}
