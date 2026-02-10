
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import StaticPool

from app.settings import settings


class Base(DeclarativeBase):
    pass


connect_args = {}
poolclass = None
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
    if settings.database_url == "sqlite:///:memory:":
        poolclass = StaticPool

engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args=connect_args,
    poolclass=poolclass,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_sqlite_columns(engine):
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.connect() as conn:
        _ensure_orbit_states_schema(conn)

        res = conn.exec_driver_sql("PRAGMA table_info(satellites)")
        columns = {row[1] for row in res}
        if "space_object_id" not in columns:
            conn.exec_driver_sql("ALTER TABLE satellites ADD COLUMN space_object_id INTEGER")

        res = conn.exec_driver_sql("PRAGMA table_info(conjunction_events)")
        columns = {row[1] for row in res}
        if "status" not in columns:
            conn.exec_driver_sql("ALTER TABLE conjunction_events ADD COLUMN status VARCHAR(32) DEFAULT 'open' NOT NULL")
        try:
            conn.exec_driver_sql("ALTER TABLE conjunction_events ADD COLUMN space_object_id INTEGER")
        except Exception:
            pass
        if "risk_tier" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE conjunction_events ADD COLUMN risk_tier VARCHAR(32) DEFAULT 'unknown' NOT NULL"
            )
        if "risk_score" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE conjunction_events ADD COLUMN risk_score FLOAT DEFAULT 0.0 NOT NULL"
            )
        if "confidence_score" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE conjunction_events ADD COLUMN confidence_score FLOAT DEFAULT 0.0 NOT NULL"
            )
        if "confidence_label" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE conjunction_events ADD COLUMN confidence_label VARCHAR(8) DEFAULT 'D' NOT NULL"
            )
        if "current_update_id" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE conjunction_events ADD COLUMN current_update_id INTEGER"
            )
        if "last_seen_at" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE conjunction_events ADD COLUMN last_seen_at DATETIME"
            )
        if "is_active" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE conjunction_events ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL"
            )
        conn.exec_driver_sql("UPDATE conjunction_events SET status = 'open' WHERE status IS NULL")
        conn.exec_driver_sql("UPDATE conjunction_events SET risk_tier = 'unknown' WHERE risk_tier IS NULL")
        conn.exec_driver_sql("UPDATE conjunction_events SET confidence_label = 'D' WHERE confidence_label IS NULL")
        conn.exec_driver_sql("UPDATE conjunction_events SET is_active = 1 WHERE is_active IS NULL")

        # NOTE: We intentionally do not drop deprecated tables in SQLite.
        # This keeps migrations safe and preserves historical data.
        res = conn.exec_driver_sql("PRAGMA table_info(decisions)")
        columns = {row[1] for row in res}
        if "status_after" not in columns:
            conn.exec_driver_sql("ALTER TABLE decisions ADD COLUMN status_after VARCHAR(32)")
        if "decision_driver" not in columns:
            conn.exec_driver_sql("ALTER TABLE decisions ADD COLUMN decision_driver VARCHAR(128)")
        if "assumption_notes" not in columns:
            conn.exec_driver_sql("ALTER TABLE decisions ADD COLUMN assumption_notes TEXT")
        if "override_reason" not in columns:
            conn.exec_driver_sql("ALTER TABLE decisions ADD COLUMN override_reason TEXT")
        if "checklist_json" not in columns:
            conn.exec_driver_sql("ALTER TABLE decisions ADD COLUMN checklist_json JSON")

        res = conn.exec_driver_sql("PRAGMA table_info(cdm_records)")
        columns = {row[1] for row in res}
        if columns:
            if "raw_path" not in columns:
                conn.exec_driver_sql("ALTER TABLE cdm_records ADD COLUMN raw_path TEXT")
            if "format" not in columns:
                conn.exec_driver_sql(
                    "ALTER TABLE cdm_records ADD COLUMN format VARCHAR(32) DEFAULT 'CCSDS_CDM_KVN' NOT NULL"
                )
            if "version" not in columns:
                conn.exec_driver_sql("ALTER TABLE cdm_records ADD COLUMN version VARCHAR(16)")
            if "originator" not in columns:
                conn.exec_driver_sql("ALTER TABLE cdm_records ADD COLUMN originator VARCHAR(128)")
            if "ref_frame" not in columns:
                conn.exec_driver_sql("ALTER TABLE cdm_records ADD COLUMN ref_frame VARCHAR(32)")
            if "object1_norad_cat_id" not in columns:
                conn.exec_driver_sql("ALTER TABLE cdm_records ADD COLUMN object1_norad_cat_id INTEGER")
            if "object2_norad_cat_id" not in columns:
                conn.exec_driver_sql("ALTER TABLE cdm_records ADD COLUMN object2_norad_cat_id INTEGER")
            conn.exec_driver_sql(
                "UPDATE cdm_records SET format = 'CCSDS_CDM_KVN' WHERE format IS NULL"
            )


def _ensure_orbit_states_schema(conn):
    res = conn.exec_driver_sql("PRAGMA table_info(orbit_states)")
    columns = {row[1]: row for row in res}
    if not columns:
        return

    expected = {
        "id",
        "satellite_id",
        "space_object_id",
        "epoch",
        "frame",
        "valid_from",
        "valid_to",
        "state_vector",
        "covariance",
        "provenance_json",
        "source_id",
        "confidence",
        "created_at",
    }
    if expected.issubset(set(columns.keys())):
        return

    has_space_object_id = "space_object_id" in columns

    conn.exec_driver_sql("PRAGMA foreign_keys=off")
    conn.exec_driver_sql("ALTER TABLE orbit_states RENAME TO orbit_states_old")
    conn.exec_driver_sql(
        """
        CREATE TABLE orbit_states (
            id INTEGER PRIMARY KEY,
            satellite_id INTEGER,
            space_object_id INTEGER,
            epoch DATETIME NOT NULL,
            frame VARCHAR(32) NOT NULL DEFAULT 'ECI',
            valid_from DATETIME,
            valid_to DATETIME,
            state_vector JSON NOT NULL,
            covariance JSON,
            provenance_json JSON,
            source_id INTEGER NOT NULL,
            confidence FLOAT NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(satellite_id) REFERENCES satellites(id),
            FOREIGN KEY(space_object_id) REFERENCES space_objects(id),
            FOREIGN KEY(source_id) REFERENCES sources(id)
        )
        """
    )
    conn.exec_driver_sql(
        f"""
        INSERT INTO orbit_states (
            id,
            satellite_id,
            space_object_id,
            epoch,
            frame,
            valid_from,
            valid_to,
            state_vector,
            covariance,
            provenance_json,
            source_id,
            confidence,
            created_at
        )
        SELECT
            id,
            satellite_id,
            {"space_object_id" if has_space_object_id else "NULL"},
            epoch,
            'ECI',
            epoch,
            NULL,
            state_vector,
            covariance,
            NULL,
            source_id,
            confidence,
            created_at
        FROM orbit_states_old
        """
    )
    conn.exec_driver_sql("DROP TABLE orbit_states_old")
    conn.exec_driver_sql("PRAGMA foreign_keys=on")
