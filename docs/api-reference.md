# API Reference

Interactive OpenAPI docs at `http://localhost:8017/docs`.

## Suggestions

### `GET /api/suggestions`

List active flexibility window suggestions for the authenticated user. Filters expired windows and returns only currently active opportunities.

### `POST /api/suggestions/{suggestion_id}/respond`

Accept or reject a flexibility suggestion. Accepting creates a commitment.

**Request body:**
```json
{
  "action": "accept"
}
```

---

## Commitments

### `POST /api/commitments`

Create a commitment directly.

### `GET /api/commitments`

List commitments for the authenticated user. Supports pagination.

**Query params:**
- `limit` — page size
- `offset` — pagination offset

### `DELETE /api/commitments/{commitment_id}`

Cancel a commitment (sets status to `cancelled`).

### `GET /api/commitments/pending`

List pending commitments for the authenticated user.

### `PATCH /api/commitments/{commitment_id}/settle`

Settle a commitment with points calculation. Sets status to `settled`.

### `GET /api/commitments/export`

Export commitments for pipeline-based data mirroring. Used by the `rec_flexibility_commitments` pipeline.

---

## Health

### `GET /health`

Service health check.
