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
- `SPACE_TRACK_USER` / `SPACE_TRACK_PASSWORD` (default: unset; enables Space-Track GP/TLE sync)
- `SPACE_TRACK_SYNC_HOURS` (default: `1`)
- `CESIUM_ION_TOKEN` (default: unset; enables Cesium Ion imagery/terrain)
- `CESIUM_NIGHT_ASSET_ID` (default: unset; optional night lights layer)
- `DEFAULT_HBR_M` (default: `10`; hard-body radius used for PoC computations)
- `POC_ALERT_THRESHOLD` (default: `1e-4`; scales PoC into a 0–1 collision risk score)
- `POC_NUM_ANGLE_STEPS` (default: `180`; angular resolution for PoC integration)

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

## CDM-Lite Ingestion (Covariance-Based PoC)

This MVP also supports ingesting a simplified CDM-like payload (relative state + combined position covariance at TCA):

```bash
curl -X POST http://127.0.0.1:8000/ingest/cdm -H 'Content-Type: application/json' -d '{
  "tca": "2026-02-04T00:00:00Z",
  "relative_position_km": [0.02, 0.0, 0.0],
  "relative_velocity_km_s": [0.0, 0.01, 0.0],
  "combined_pos_covariance_km2": [[1,0,0],[0,1,0],[0,0,1]],
  "hard_body_radius_m": 10,
  "source": {"name": "cdm", "type": "public"},
  "satellite": {"name": "Alpha", "orbit_regime": "LEO", "status": "active"},
  "secondary_norad_cat_id": 12345,
  "secondary_name": "CATALOG-OBJECT"
}'
```

## Catalog Sync

```bash
curl -X POST http://127.0.0.1:8000/catalog/sync
curl http://127.0.0.1:8000/catalog/status
```
