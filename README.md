# SpaceProject MVP

A minimal FastAPI implementation of an **operator-first orbital conjunction decision-support tool**. This repository provides:

- Multi-source orbit state ingestion with provenance
- TLE + SGP4 short-horizon screening (≤14 days)
- Conjunction event deduplication + evolution history
- Screening-level risk tiering (Low / Watch / High) with explicit confidence
- Human-in-the-loop decisions with audit logging
- API and lightweight dashboard UI

## Quick Start

```bash
python3 -m venv .venv
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
- `SPACE_TRACK_USER` / `SPACE_TRACK_PASSWORD` (default: unset; enables Space-Track GP/TLE sync)
- `SPACE_TRACK_SYNC_HOURS` (default: `1`)
- `CESIUM_ION_TOKEN` (default: unset; enables Cesium Ion imagery/terrain)
- `CESIUM_NIGHT_ASSET_ID` (default: unset; optional night lights layer)
- `SESSION_SECRET` (default: dev value; set to a long random string)
- `BUSINESS_ACCESS_CODE` (default: unset; enables business-only tabs and APIs)
- `SCREENING_HORIZON_DAYS` (default: `14`)
- `SCREENING_VOLUME_KM` (default: `10.0`)
- `TIME_CRITICAL_HOURS` (default: `72`)
- `TLE_MAX_AGE_HOURS_FOR_CONFIDENCE` (default: `72`)
- `ORBIT_STATE_RETENTION_DAYS` (default: `30`)
- `TLE_RECORD_RETENTION_DAYS` (default: `90`)

## Best Public TLE Accuracy (Space-Track)

If you want the best public TLE freshness (and the most accurate results you can get from TLE+SGP4):

1. Create a (free) Space-Track account.
2. Copy `.env.example` to `.env` and set `SPACE_TRACK_USER` + `SPACE_TRACK_PASSWORD`.
3. Keep `SPACE_TRACK_SYNC_HOURS=1` (Space-Track recommends at most 1 GP/TLE download per hour).
4. Start the app; it will sync in the background, and you can also use the **Sync TLEs** button in the viewer (it only syncs if due).

## API Docs

- OpenAPI/Swagger: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## Notes

This MVP is a screening tool: it does not claim high-precision prediction. It is intentionally designed for clarity and extendability over physical fidelity.


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

## CDM-Lite Attachment

This MVP supports attaching a simplified CDM-like payload (relative state + combined position covariance at TCA) to an existing event:

```bash
curl -X POST http://127.0.0.1:8000/events/1/cdm -H 'Content-Type: application/json' -d '{
  "tca": "2026-02-04T00:00:00Z",
  "relative_position_km": [0.02, 0.0, 0.0],
  "relative_velocity_km_s": [0.0, 0.01, 0.0],
  "combined_pos_covariance_km2": [[1,0,0],[0,1,0],[0,0,1]],
  "hard_body_radius_m": 10,
  "source": {"name": "cdm", "type": "public"},
  "override_secondary": true
}'
```

## Catalog Sync

```bash
curl -X POST http://127.0.0.1:8000/catalog/sync
curl http://127.0.0.1:8000/catalog/status
```
