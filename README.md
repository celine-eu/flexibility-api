# CELINE Flexibility API

Backend service for the REC flexibility model. Manages flexibility windows (suggestions), user commitments, settlement, and gamification points. Used via the `celine-webapp` BFF.

## Features

- Flexibility window suggestions with active/expired window management
- User commitment lifecycle: create, list, cancel, settle
- Gamification points calculation based on commitment fulfillment
- Export API for pipeline-based data mirroring
- Settlement of commitments with points assignment
- MQTT pipeline listener for automated nudge scheduling
- OPA-enforced access control via `celine-sdk`

## Quick Start

```bash
uv sync
task alembic:migrate
task run
# Listens on http://localhost:8017
```

## API

| Group | Endpoints |
|---|---|
| **suggestions** | `GET /api/suggestions` — list active flexibility windows |
| | `POST /api/suggestions/{id}/respond` — accept/reject a suggestion |
| **commitments** | `POST /api/commitments` — create a commitment |
| | `GET /api/commitments` — list commitments (paginated) |
| | `DELETE /api/commitments/{id}` — cancel a commitment |
| | `GET /api/commitments/pending` — list pending commitments |
| | `PATCH /api/commitments/{id}/settle` — settle with points |
| | `GET /api/commitments/export` — export for pipeline mirroring |
| **health** | `GET /health` |

## Configuration

### Posture: `CELINE_ENV=dev` is required for the dev defaults

The defaults below are **development** defaults (the local database password, a client
secret equal to the client id, the local Keycloak). They are accepted only when the
process environment says `CELINE_ENV=dev` (or `ENVIRONMENT=dev`). **Unset means
hardened**: so does `staging`, `prod` or anything else. Outside dev the service refuses
to start (`InsecureConfiguration`, listing every offending variable) while any of them is
still in use or the policy bundle did not load, and a policy engine that is missing or
raises denies instead of allowing. See REQ-0011 and REQ-0055.

- `task run` / `task debug` export `CELINE_ENV=dev` for you.
- `CELINE_ENV=staging task run` is the prod-like local mode; set real values first.
- The local `docker compose` api service passes no environment; a container started
  that way is hardened.
- The check reads the real process environment, not `.env` via pydantic.

This needs `celine.sdk.posture`, which is **not yet in a released celine-sdk** — the
next release after 1.24.0. Until then it works only against an editable SDK checkout.

| Variable | Default | Description |
|---|---|---|
| `CELINE_ENV` | — (unset = hardened) | `dev` relaxes the posture checks; anything else is hardened |
| `DATABASE_URL` | `postgresql+asyncpg://...host.docker.internal:15432/flexibility` | PostgreSQL async URL |
| `DB_SCHEMA` | `flexibility` | Database schema |
| `NUDGING_API_URL` | `http://host.docker.internal:8016` | nudging-tool URL |
| `DIGITAL_TWIN_API_URL` | `http://host.docker.internal:8002` | Digital Twin URL |
| `REC_REGISTRY_URL` | `http://host.docker.internal:8004` | REC registry URL |
| `JWT_HEADER_NAME` | `x-auth-request-access-token` | JWT header from oauth2_proxy |
| `DT_CLIENT_SCOPE` | — | OIDC scope for DT calls |
| `REC_REGISTRY_SCOPE` | — | OIDC scope for registry calls |
| `NUDGING_SCOPE` | — | OIDC scope for nudging calls |
| `CELINE_OIDC_*` | (from celine-sdk) | OIDC settings (audience and client id: `svc-flexibility`) — `CELINE_OIDC_BASE_URL` and `CELINE_OIDC_JWKS_URI` must be set outside dev |
| `CELINE_OIDC_CLIENT_SECRET` | `svc-flexibility` (dev only) | Must differ from the client id outside dev |
| `MQTT__*` | (from celine-sdk) | MQTT settings |

## Taskfile Commands

| Command | Description |
|---|---|
| `task run` | Start dev server on port 8017 |
| `task debug` | Start with debugger (port 48017) |
| `task test` | Run pytest (needs nothing running; `FLEXIBILITY_IT_KEYCLOAK=1` adds the real-token tests against a local Keycloak) |
| `task alembic:migrate` | Apply pending migrations |
| `task alembic:sync-model` | Generate new migration |
| `task alembic:check` | Fail if the models have drifted from the migrations |
| `task alembic:reset` | Reset DB to base |
| `task release` | Run semantic-release |

## Project Layout

```
src/celine/flexibility/
  main.py                        # FastAPI app factory (create_app)
  core/config.py                 # Pydantic settings
  api/
    suggestions.py               # Suggestion/window endpoints
    commitments.py               # Commitment CRUD + settlement
    deps.py                      # FastAPI dependencies
  models/
    commitment.py                # SQLAlchemy ORM: Commitment
  schemas/
    commitment.py                # Pydantic schemas for commitments
    suggestion.py                # Pydantic schemas for suggestions
  services/
    settlement.py                # Commitment settlement + points
    nudge_opportunity.py         # Nudge on new flexibility opportunity
    schedule_nudge.py            # Nudge scheduling service
    reminders.py                 # Flexibility reminders
    pipeline_listener.py         # MQTT listener for pipeline events
  security/
    auth.py                      # JWT authentication
    middleware.py                # Auth middleware
    policy.py                    # OPA policy evaluation
policies/                        # OPA .rego policy files
alembic/                         # Database migrations
```

## License

Apache 2.0 — Copyright © 2025 Spindox Labs
