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

reason := "user accessing own commitment" if {
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

reason := "service access granted" if {
    is_service
    allow
}

# ── denials ───────────────────────────────────────────────────────────────────

reason := "not resource owner" if {
    not allow
    not is_service
    not is_owner
}

reason := "missing flexibility scope" if {
    not allow
    not has_any_scope(["flexibility.read", "flexibility.write", "flexibility.admin"])
}

reason := "service account required" if {
    not allow
    not is_service
    input.action.name == "service"
}
