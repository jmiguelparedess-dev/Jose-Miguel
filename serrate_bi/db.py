"""Capa de persistencia: SQLite con WAL, claves foráneas y esquema versionado.

Las tablas separan explícitamente:
  - companies / executives           -> entidades
  - field_state                      -> valor vigente de cada dato + confianza + fuente + fecha
  - facts                            -> TODAS las observaciones (nunca se borran)
  - sources                          -> fuentes consultadas (URL, tipo, fecha)
  - projects / news                  -> proyectos y actividad reciente
  - activities / outreach_drafts     -> actividad comercial (CRM) y borradores de contacto
  - change_log                       -> qué dato cambió, cuándo y por qué
  - research_runs                    -> trabajos de investigación / actualización
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    core_key TEXT NOT NULL,
    legal_name TEXT,
    activity TEXT,
    sector TEXT,
    city TEXT,
    department TEXT,
    website TEXT,
    domain TEXT,
    phone TEXT,
    email TEXT,
    linkedin_url TEXT,
    description TEXT,
    crm_status TEXT NOT NULL DEFAULT 'Nuevo prospecto',
    score INTEGER,
    score_reason TEXT,
    analysis_json TEXT,
    analyzed_at TEXT,
    next_follow_up TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_researched_at TEXT,
    last_verified_at TEXT,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_companies_name_key ON companies(name_key);
CREATE INDEX IF NOT EXISTS ix_companies_domain ON companies(domain);
CREATE INDEX IF NOT EXISTS ix_companies_core_key ON companies(core_key);

CREATE TABLE IF NOT EXISTS executives (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    full_name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    position TEXT,
    email TEXT,
    phone TEXT,
    linkedin_url TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_verified_at TEXT,
    UNIQUE(company_id, name_key)
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    url TEXT,
    title TEXT,
    publisher TEXT,
    source_type TEXT NOT NULL,
    snippet TEXT,
    first_retrieved_at TEXT NOT NULL,
    last_retrieved_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sources_url ON sources(url) WHERE url IS NOT NULL;

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    value_key TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(id),
    origin TEXT NOT NULL,
    evidence TEXT,
    run_id INTEGER,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_facts_entity ON facts(entity_type, entity_id, field);
CREATE INDEX IF NOT EXISTS ix_facts_company ON facts(company_id);

CREATE TABLE IF NOT EXISTS field_state (
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    value_key TEXT NOT NULL,
    confidence TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(id),
    origin TEXT NOT NULL,
    run_id INTEGER,
    verified_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (entity_type, entity_id, field)
);

CREATE TABLE IF NOT EXISTS change_log (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    field TEXT NOT NULL,
    change_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    old_confidence TEXT,
    new_confidence TEXT,
    source_id INTEGER REFERENCES sources(id),
    origin TEXT NOT NULL,
    run_id INTEGER,
    changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_change_company ON change_log(company_id, changed_at);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    project_type TEXT,
    city TEXT,
    description TEXT,
    status TEXT,
    confidence TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(id),
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    UNIQUE(company_id, name_key)
);

CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    publisher TEXT,
    published_at TEXT,
    summary TEXT,
    categories TEXT,
    source_id INTEGER REFERENCES sources(id),
    first_seen_at TEXT NOT NULL,
    UNIQUE(company_id, url)
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    executive_id INTEGER REFERENCES executives(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    content TEXT,
    activity_date TEXT NOT NULL,
    meeting_at TEXT,
    follow_up_date TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_activities_company ON activities(company_id);

CREATE TABLE IF NOT EXISTS outreach_drafts (
    id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    executive_id INTEGER REFERENCES executives(id) ON DELETE SET NULL,
    channel TEXT NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'borrador',
    generator TEXT NOT NULL,
    basis_json TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS research_runs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    params TEXT,
    status TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    summary TEXT,
    log TEXT NOT NULL DEFAULT '',
    stats_json TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_db_path() -> str:
    base = os.environ.get("SERRATE_BI_DATA", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "serrate_bi.db")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)  # autocommit; transacciones explícitas
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO NOTHING", (str(SCHEMA_VERSION),))
        # Trabajos que quedaron "en curso" si la aplicación se cerró abruptamente.
        conn.execute("UPDATE research_runs SET status='interrumpido', finished_at=? "
                     "WHERE status IN ('en curso','en cola')", (now_iso(),))
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
