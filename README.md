# SpaceProject MVP

A minimal FastAPI implementation of the **Autonomous Space Risk & Collision Avoidance Platform** MVP. This repository provides:

- Multi-source orbit state ingestion with provenance
- Conjunction detection and risk scoring
- Maneuver recommendation generation
- Human-in-the-loop decisions with audit logging
- API and minimal dashboard UI

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

App runs at `http://127.0.0.1:8000`.

## Environment

Optional environment variables (see `app/settings.py`):

- `DATABASE_URL` (default: `sqlite:///./spaceops.db`)
- `RAW_DATA_DIR` (default: `./data/raw`)
- `WEBHOOK_TIMEOUT_SECONDS` (default: `3.0`)
- `CELESTRAK_GROUP` (default: `active`)
- `CATALOG_SYNC_HOURS` (default: `24`)
- `CATALOG_MAX_OBJECTS` (default: unset)

## API Docs

- OpenAPI/Swagger: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## Notes

This MVP uses simplified physics models for risk and conjunctions. It is intentionally designed for clarity and extendability over physical fidelity.


## Example Ingestion

```bash
curl -X POST http://127.0.0.1:8000/ingest/orbit-state   -H 'Content-Type: application/json'   -d '{
    "epoch": "2026-02-04T00:00:00Z",
    "state_vector": [7000, 0, 0, 0, 7.5, 0],
    "confidence": 0.7,
    "source": {"name": "public-tle", "type": "public"},
    "satellite": {"name": "Alpha", "orbit_regime": "LEO", "status": "active"}
  }'
```

## Catalog Sync

```bash
curl -X POST http://127.0.0.1:8000/catalog/sync
curl http://127.0.0.1:8000/catalog/status
```
