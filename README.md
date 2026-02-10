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
export BUSINESS_ACCESS_CODE=dev-code
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
- `BUSINESS_ACCESS_CODE` (default: unset; required; gates UI + API behind login)
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

- Health (no auth): `http://127.0.0.1:8000/healthz`
- OpenAPI/Swagger (business login required): `http://127.0.0.1:8000/docs`
- ReDoc (business login required): `http://127.0.0.1:8000/redoc`

## Notes

This MVP is a screening tool: it does not claim high-precision prediction. It is intentionally designed for clarity and extendability over physical fidelity.

- Frames: internal relative computations are done in `GCRS`. `TEME` states (SGP4 output) are converted via `astropy`. For MVP, `ECI` / `GCRF` / `EME2000` / `J2000` inputs are treated as `GCRS`-like (approximation; document any operational requirements before relying on it).
- Astropy IERS auto-download is disabled at runtime; some transforms may use degraded accuracy outside bundled IERS coverage.


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

## CCSDS CDM (KVN) Attachment

This MVP supports attaching a CCSDS CDM (KVN text) to an existing event. If covariance is present, confidence is boosted (still no Pc; this is screening-level decision support).

```bash
curl -X POST http://127.0.0.1:8000/events/1/cdm \\
  -H 'Content-Type: text/plain' \\
  --data-binary @- <<'CDM'
CCSDS_CDM_VERS = 1.0
CREATION_DATE = 2026-02-10T00:00:00Z
ORIGINATOR = TEST
TCA = 2026-02-11T12:34:56Z
REF_FRAME = GCRS
MISS_DISTANCE = 20.0 [m]
RELATIVE_SPEED = 10.0 [m/s]
OBJECT = OBJECT1
NORAD_CAT_ID = 10000
OBJECT_NAME = ALPHA
X = 7000.0 [km]
Y = 0.0 [km]
Z = 0.0 [km]
X_DOT = 0.0 [km/s]
Y_DOT = 7.5 [km/s]
Z_DOT = 0.0 [km/s]
OBJECT = OBJECT2
NORAD_CAT_ID = 12345
OBJECT_NAME = CATALOG-DELTA
X = 7000.02 [km]
Y = 0.0 [km]
Z = 0.0 [km]
X_DOT = 0.0 [km/s]
Y_DOT = 7.51 [km/s]
Z_DOT = 0.0 [km/s]
CR_R = 100.0 [m^2]
CT_R = 0.0 [m^2]
CT_T = 100.0 [m^2]
CN_R = 0.0 [m^2]
CN_T = 0.0 [m^2]
CN_N = 100.0 [m^2]
CDM
```

### CDM Inbox (auto-create/dedupe)

If you don't already have an event ID, you can post the same KVN to the inbox endpoint. The server will:

- Identify the operator satellite from `OBJECT1`/`OBJECT2`
- Create/dedupe a `ConjunctionEvent` (one event, many updates)
- Append a `ConjunctionEventUpdate` + store the raw CDM snapshot

```bash
curl -X POST http://127.0.0.1:8000/cdm/inbox \\
  -H 'Content-Type: text/plain' \\
  --data-binary @cdm_message.kvn
```

## Webhooks

Register a webhook subscription (business session required). Event types:

- `conjunction.changed` (tier or confidence label changed)
- `conjunction.created` (new event created via inbox)
- `screening.completed` (screening run summary)

Example (login + create subscription):

```bash
curl -c cookies.txt -X POST http://127.0.0.1:8000/auth/login \\
  -d 'access_code=dev-code&next=/dashboard'

curl -b cookies.txt -X POST http://127.0.0.1:8000/webhooks \\
  -H 'Content-Type: application/json' \\
  -d '{\"url\":\"https://example.com/webhook\",\"event_type\":\"conjunction.changed\",\"secret\":\"optional-shared-secret\"}'
```

## Catalog Sync

```bash
curl -X POST http://127.0.0.1:8000/catalog/sync
curl http://127.0.0.1:8000/catalog/status
```
