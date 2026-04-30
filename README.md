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

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://...host.docker.internal:15432/flexibility` | PostgreSQL async URL |
| `DB_SCHEMA` | `flexibility` | Database schema |
| `NUDGING_API_URL` | `http://host.docker.internal:8016` | nudging-tool URL |
| `DIGITAL_TWIN_API_URL` | `http://host.docker.internal:8002` | Digital Twin URL |
| `REC_REGISTRY_URL` | `http://host.docker.internal:8004` | REC registry URL |
| `JWT_HEADER_NAME` | `x-auth-request-access-token` | JWT header from oauth2_proxy |
| `DT_CLIENT_SCOPE` | — | OIDC scope for DT calls |
| `REC_REGISTRY_SCOPE` | — | OIDC scope for registry calls |
| `NUDGING_SCOPE` | — | OIDC scope for nudging calls |
| `OIDC__*` | (from celine-sdk) | OIDC settings (audience: `svc-flexibility`) |
| `MQTT__*` | (from celine-sdk) | MQTT settings |

## Taskfile Commands

| Command | Description |
|---|---|
| `task run` | Start dev server on port 8017 |
| `task debug` | Start with debugger (port 48017) |
| `task test` | Run pytest |
| `task alembic:migrate` | Apply pending migrations |
| `task alembic:revision` | Generate new migration |
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
