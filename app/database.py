
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
        res = conn.exec_driver_sql("PRAGMA table_info(conjunction_events)")
        columns = {row[1] for row in res}
        if "status" not in columns:
            conn.exec_driver_sql("ALTER TABLE conjunction_events ADD COLUMN status VARCHAR(32) DEFAULT 'open' NOT NULL")
        try:
            conn.exec_driver_sql("ALTER TABLE conjunction_events ADD COLUMN space_object_id INTEGER")
        except Exception:
            pass
        conn.exec_driver_sql("UPDATE conjunction_events SET status = 'open' WHERE status IS NULL")
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


def _ensure_orbit_states_schema(conn):
    res = conn.exec_driver_sql("PRAGMA table_info(orbit_states)")
    columns = {row[1]: row for row in res}
    if not columns:
        return

    satellite_not_null = columns.get("satellite_id", (None, None, None, 0))[3] == 1
    has_space_object = "space_object_id" in columns

    if not satellite_not_null and has_space_object:
        return

    conn.exec_driver_sql("PRAGMA foreign_keys=off")
    conn.exec_driver_sql("ALTER TABLE orbit_states RENAME TO orbit_states_old")
    conn.exec_driver_sql(
        """
        CREATE TABLE orbit_states (
            id INTEGER PRIMARY KEY,
            satellite_id INTEGER,
            space_object_id INTEGER,
            epoch DATETIME NOT NULL,
            state_vector JSON NOT NULL,
            covariance JSON,
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
        """
        INSERT INTO orbit_states (
            id, satellite_id, epoch, state_vector, covariance, source_id, confidence, created_at
        )
        SELECT id, satellite_id, epoch, state_vector, covariance, source_id, confidence, created_at
        FROM orbit_states_old
        """
    )
    conn.exec_driver_sql("DROP TABLE orbit_states_old")
    conn.exec_driver_sql("PRAGMA foreign_keys=on")
