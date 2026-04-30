# Architecture

## Overview

The flexibility-api manages the lifecycle of energy flexibility windows and user commitments within a REC. It receives flexibility window data from pipeline events, presents active windows as suggestions to users, tracks commitments (accept/reject), handles settlement, and calculates gamification points.

```
Pipeline run (MQTT) -> pipeline_listener -> schedule_nudge -> nudging-tool
                                        -> settlement service

User (via webapp BFF) -> suggestions API -> flexibility windows
                      -> commitments API -> create/cancel/list
```

## Service Dependencies

| Service | Usage |
|---|---|
| **Digital Twin** | Flexibility window data queries |
| **nudging-tool** | Send flexibility opportunity and reminder notifications |
| **rec-registry** | Resolve community members for broadcast nudges |
| **Keycloak** | Identity provider (via oauth2_proxy) |
| **MQTT broker** | Pipeline completion events (for nudge scheduling) |

## Database

PostgreSQL (async via SQLAlchemy + asyncpg). Main model:

- `Commitment` — user commitment to a flexibility window: user_id, community_id, suggestion details, status (pending/committed/settled/cancelled), points, settlement timestamp

Alembic manages migrations.

## Pipeline Listener

`services/pipeline_listener.py` subscribes to MQTT pipeline events. When a flexibility pipeline run completes, it triggers nudge scheduling for new flexibility opportunities.

## Settlement

`services/settlement.py` processes commitment settlement:
1. Checks if the commitment window has passed
2. Calculates points based on fulfillment
3. Updates commitment status to `settled`
