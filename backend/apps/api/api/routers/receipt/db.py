"""SQLite engine + FastAPI session dependency for Receipt v0.

File path: backend/data/receipt.db — resolved from this module's location so
behavior is identical under uvicorn, pytest, and Docker.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

# apps/api/api/routers/receipt/db.py -> parents[5] == backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[5]
_DEFAULT_DB_PATH = _BACKEND_ROOT / "data" / "receipt.db"

# Allow override via env (used by tests and docker).
_DB_URL = os.environ.get("RECEIPT_DB_URL") or f"sqlite:///{_DEFAULT_DB_PATH}"

if _DB_URL.startswith("sqlite:///") and _DB_URL != "sqlite:///:memory:":
    Path(_DB_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    _DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)


def init_db() -> None:
    """Create tables if absent. Safe to call repeatedly (idempotent).

    Also runs any additive ALTER TABLE migrations needed for schema drift
    (SQLModel.create_all only creates MISSING tables — it ignores added
    columns on existing tables). Keep this tiny; for real migrations
    we'll need Alembic.
    """
    from apps.api.api.models import webhooks as webhook_models  # noqa: F401
    from apps.api.api.routers.billing import models as billing_models  # noqa: F401

    from . import models  # noqa: F401 — registers SQLModel tables
    from .auth_sessions_model import AuthSession as _AS  # noqa: F401
    # Local-auth user table (used when AUTH_PROVIDER=local).
    from apps.api.api.services.auth.local_models import AuthUser as _AU  # noqa: F401
    # Local document store backing the Yoru surface (AUTH_PROVIDER=local).
    from libs.datastore.local_store import DataRecord as _DR  # noqa: F401
    SQLModel.metadata.create_all(engine)

    # Additive column migrations — idempotent via PRAGMA introspection.
    with engine.begin() as conn:
        from sqlalchemy import text
        rows = conn.execute(text("PRAGMA table_info(sessions)")).fetchall()
        cols = {r[1] for r in rows}  # r[1] = column name
        if "title" not in cols:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN title VARCHAR"))
        # Phase C: routing target + context snapshot per session.
        # Phase W1: rename org_id → workspace_id (routing now targets a workspace,
        # not an organization). Idempotent via column-presence check.
        if "org_id" in cols and "workspace_id" not in cols:
            conn.execute(text("ALTER TABLE sessions RENAME COLUMN org_id TO workspace_id"))
            # Refresh our knowledge of the column set after the rename.
            rows = conn.execute(text("PRAGMA table_info(sessions)")).fetchall()
            cols = {r[1] for r in rows}
        if "workspace_id" not in cols:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN workspace_id TEXT"))
        conn.execute(text("DROP INDEX IF EXISTS ix_sessions_org_id"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sessions_workspace_id ON sessions(workspace_id)"))
        if "cwd" not in cols:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN cwd TEXT"))
        if "git_remote" not in cols:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN git_remote TEXT"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sessions_git_remote ON sessions(git_remote)"))
        if "git_branch" not in cols:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN git_branch TEXT"))
        # S2 (design trovex:28547568 §3): compliance retention expiry.
        if "retention_expires_at" not in cols:
            conn.execute(text("ALTER TABLE sessions ADD COLUMN retention_expires_at TIMESTAMP"))

        # Phase W1: backfill workspace_id from the old org_id → new workspace_id
        # mapping stored on organizations.settings->'migration_workspace_id'.
        # Runs exactly once per (old_org_id, new_workspace_id) pair via a
        # boolean marker row in a tiny key-value table; subsequent restarts
        # skip the backfill.
        _run_workspace_id_backfill_once(conn)

        # Phase C: events.cwd / git_remote / git_branch.
        rows = conn.execute(text("PRAGMA table_info(events)")).fetchall()
        ev_cols = {r[1] for r in rows}
        if "cwd" not in ev_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN cwd TEXT"))
        if "git_remote" not in ev_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN git_remote TEXT"))
        if "git_branch" not in ev_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN git_branch TEXT"))
        # Tamper-evident hash chain columns.
        if "entry_hash" not in ev_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN entry_hash TEXT"))
        if "prev_hash" not in ev_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN prev_hash TEXT"))
        if "entry_uuid" not in ev_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN entry_uuid TEXT"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_events_entry_uuid ON events(entry_uuid)"
            ))
        # S2 (design trovex:28547568 §3): compliance retention expiry. The
        # event_flags table itself is created by create_all above (new model);
        # only these additive columns need a hand-applied ALTER on live rows.
        if "retention_expires_at" not in ev_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN retention_expires_at TIMESTAMP"))
        # Chain v2 (S3, design trovex:28547568 §2): commit-to-digest columns.
        # Additive + backward-compatible — existing rows keep chain_version
        # NULL (verified as legacy v0 over plaintext); new writes are v2.
        if "chain_version" not in ev_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN chain_version INTEGER"))
        if "content_digest" not in ev_cols:
            conn.execute(text("ALTER TABLE events ADD COLUMN content_digest TEXT"))

        # Multi-tenant tenant key (design 44a3774a §2 / M1). Add `org_id` to the
        # three audit tables + index each, then backfill legacy un-scoped rows to
        # the default org exactly once. Additive + idempotent.
        #
        # NB: the sessions index MUST be (re)created here — AFTER the
        # `DROP INDEX IF EXISTS ix_sessions_org_id` cleanup above (that drop is
        # leftover from the historic org_id→workspace_id RENAME and runs every
        # boot). Creating it here means it survives; the org_id model fields
        # intentionally omit index=True for the same reason.
        for _tbl in ("sessions", "events", "event_flags"):
            _rows = conn.execute(text(f"PRAGMA table_info({_tbl})")).fetchall()
            _tcols = {r[1] for r in _rows}
            if "org_id" not in _tcols:
                conn.execute(text(f"ALTER TABLE {_tbl} ADD COLUMN org_id TEXT"))
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS ix_{_tbl}_org_id ON {_tbl}(org_id)"
            ))
        _run_orgid_backfill_once(conn)

        # steal#6 (study 6dcc43ce): git context on red-flag records. Denormalize
        # the parent session's git fields onto event_flags so the risk log is
        # self-explanatory without a join. Additive + idempotent; existing rows
        # keep NULL (they predate capture). No backfill — historic flags have no
        # frozen git context to recover.
        _ef_cols = {
            r[1]
            for r in conn.execute(text("PRAGMA table_info(event_flags)")).fetchall()
        }
        for _col in ("git_branch", "git_remote", "cwd"):
            if _col not in _ef_cols:
                conn.execute(text(f"ALTER TABLE event_flags ADD COLUMN {_col} TEXT"))

        # Phase B migration: hook_tokens → cli_tokens + type split. Idempotent.
        has_cli = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cli_tokens'"
        )).first() is not None
        has_hook = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hook_tokens'"
        )).first() is not None
        if not has_cli and has_hook:
            conn.execute(text("ALTER TABLE hook_tokens RENAME TO cli_tokens"))
            has_cli = True
        if has_cli:
            rows = conn.execute(text("PRAGMA table_info(cli_tokens)")).fetchall()
            cli_cols = {r[1] for r in rows}
            if "token_type" not in cli_cols:
                conn.execute(text("ALTER TABLE cli_tokens ADD COLUMN token_type TEXT DEFAULT 'user'"))
            # Phase W1: service tokens target workspace, not org.
            if "org_id" in cli_cols and "workspace_id" not in cli_cols:
                conn.execute(text("ALTER TABLE cli_tokens RENAME COLUMN org_id TO workspace_id"))
                rows = conn.execute(text("PRAGMA table_info(cli_tokens)")).fetchall()
                cli_cols = {r[1] for r in rows}
            if "workspace_id" not in cli_cols:
                conn.execute(text("ALTER TABLE cli_tokens ADD COLUMN workspace_id TEXT"))
            if "minted_by_user_id" not in cli_cols:
                conn.execute(text("ALTER TABLE cli_tokens ADD COLUMN minted_by_user_id TEXT"))
                # Backfill: legacy tokens were self-minted (old unauth endpoint
                # trusted body.user, so the "minter" and the "user" are the same).
                conn.execute(text(
                    "UPDATE cli_tokens SET minted_by_user_id = user "
                    "WHERE minted_by_user_id IS NULL"
                ))
            if "machine_hostname" not in cli_cols:
                conn.execute(text("ALTER TABLE cli_tokens ADD COLUMN machine_hostname TEXT"))
            # Multi-dev identity model (DEC-yoru-design-ruling-1 A.3#1).
            if "identity_label" not in cli_cols:
                conn.execute(text("ALTER TABLE cli_tokens ADD COLUMN identity_label TEXT"))
            if "scopes" not in cli_cols:
                conn.execute(text("ALTER TABLE cli_tokens ADD COLUMN scopes TEXT"))
            if "expires_at" not in cli_cols:
                conn.execute(text("ALTER TABLE cli_tokens ADD COLUMN expires_at TEXT"))
            # Multi-tenant tenant key (M2, design 44a3774a §4). token = (user,
            # org_id). Added AFTER the org_id→workspace_id rename above, so the
            # rename guard never eats it (workspace_id is present by here). This
            # is the distinct TENANT org, NOT the old routing column.
            if "org_id" not in cli_cols:
                conn.execute(text("ALTER TABLE cli_tokens ADD COLUMN org_id TEXT"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_cli_tokens_org_id ON cli_tokens(org_id)"
            ))
            # Multi-dev identity model (DEC-yoru-design-ruling-1 A.3#2) —
            # fallback-only workspace resolution, see the model docstring.
            if "default_org_id" not in cli_cols:
                conn.execute(text("ALTER TABLE cli_tokens ADD COLUMN default_org_id TEXT"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_cli_tokens_default_org_id ON cli_tokens(default_org_id)"
            ))

        # Multi-dev identity model (DEC-yoru-design-ruling-1 A.3#1): the raw
        # machine hostname, distinct from the user-overridable `label`.
        has_da = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='device_authorizations'"
        )).first() is not None
        if has_da:
            da_cols = {
                r[1] for r in conn.execute(text("PRAGMA table_info(device_authorizations)")).fetchall()
            }
            if "hostname" not in da_cols:
                conn.execute(text("ALTER TABLE device_authorizations ADD COLUMN hostname TEXT"))
            # A.3#3: server-issued CliToken.id, round-tripped to the CLI so
            # it can key its local identity slot.
            if "cli_token_id" not in da_cols:
                conn.execute(text("ALTER TABLE device_authorizations ADD COLUMN cli_token_id TEXT"))

        # Multi-tenant tenant key on api_keys (M2). Additive; the table is
        # created by create_all above, so only the ALTER + index need a hand.
        has_ak = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys'"
        )).first() is not None
        if has_ak:
            ak_cols = {
                r[1] for r in conn.execute(text("PRAGMA table_info(api_keys)")).fetchall()
            }
            if "org_id" not in ak_cols:
                conn.execute(text("ALTER TABLE api_keys ADD COLUMN org_id TEXT"))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_api_keys_org_id ON api_keys(org_id)"
            ))


def get_session() -> Iterator[Session]:
    """FastAPI dependency — yields a SQLModel Session."""
    with Session(engine) as session:
        yield session


def _run_workspace_id_backfill_once(conn) -> None:
    """Map old sessions.org_id (→ Supabase orgs.id UUIDs) to the new workspace
    UUIDs we minted during the workspaces_schema migration. The mapping lives
    on organizations.settings->'migration_workspace_id' and is fetched via a
    live HTTP call to Supabase PostgREST using the anon key.

    Idempotent: guarded by a marker row so a second restart is a no-op even
    if Supabase is unreachable the first time (backfill will retry next boot
    if `_workspace_backfill_done` is still false).
    """
    from sqlalchemy import text as sql_text

    conn.execute(sql_text(
        "CREATE TABLE IF NOT EXISTS _schema_markers "
        "(key TEXT PRIMARY KEY, value TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP)"
    ))
    done = conn.execute(sql_text(
        "SELECT value FROM _schema_markers WHERE key = '_workspace_backfill_done'"
    )).first()
    if done and done[0] == "1":
        return

    # Any session rows with a non-null workspace_id that still look like an
    # old org UUID? Heuristic: compare against Supabase. If none need
    # remapping, mark done and exit.
    any_rows = conn.execute(sql_text(
        "SELECT COUNT(*) FROM sessions WHERE workspace_id IS NOT NULL"
    )).first()
    if not any_rows or any_rows[0] == 0:
        conn.execute(sql_text(
            "INSERT OR REPLACE INTO _schema_markers (key, value) VALUES ('_workspace_backfill_done', '1')"
        ))
        return

    import os
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon = os.environ.get("SUPABASE_ANON_KEY", "")
    if not supabase_url or not anon:
        # Can't backfill without creds — leave marker unset so we retry later.
        return

    import httpx
    try:
        resp = httpx.get(
            f"{supabase_url}/rest/v1/organizations",
            headers={"apikey": anon, "Authorization": f"Bearer {anon}"},
            params={"select": "id,settings"},
            timeout=5.0,
        )
        resp.raise_for_status()
        orgs = resp.json()
    except Exception:
        return  # retry next boot

    mapping: dict[str, str] = {}
    for o in orgs:
        settings = o.get("settings") or {}
        new_ws = settings.get("migration_workspace_id")
        if new_ws:
            mapping[o["id"]] = new_ws

    if not mapping:
        conn.execute(sql_text(
            "INSERT OR REPLACE INTO _schema_markers (key, value) VALUES ('_workspace_backfill_done', '1')"
        ))
        return

    # Remap sessions rows where workspace_id is an old org id.
    for old, new in mapping.items():
        conn.execute(
            sql_text("UPDATE sessions SET workspace_id = :new WHERE workspace_id = :old"),
            {"new": new, "old": old},
        )
        conn.execute(
            sql_text("UPDATE cli_tokens SET workspace_id = :new WHERE workspace_id = :old"),
            {"new": new, "old": old},
        )

    conn.execute(sql_text(
        "INSERT OR REPLACE INTO _schema_markers (key, value) VALUES ('_workspace_backfill_done', '1')"
    ))


def _run_orgid_backfill_once(conn) -> None:
    """Backfill the multi-tenant `org_id` onto legacy un-scoped audit rows.

    Every session/event/event_flag written before multi-tenant (M1) has a NULL
    org_id. Assign them all to the default org (``DEFAULT_ORG_ID`` — the
    studio's own / legacy org, design 44a3774a §2). New writes stamp org_id from
    the acting dev's org-bound token at ingest (M2), so this only ever touches
    pre-M1 rows.

    Idempotent: a done-marker makes a second boot a no-op. Purely local
    (no network), unlike the workspace backfill.
    """
    from sqlalchemy import text as sql_text

    from .models import DEFAULT_ORG_ID

    conn.execute(sql_text(
        "CREATE TABLE IF NOT EXISTS _schema_markers "
        "(key TEXT PRIMARY KEY, value TEXT, ts TEXT DEFAULT CURRENT_TIMESTAMP)"
    ))
    done = conn.execute(sql_text(
        "SELECT value FROM _schema_markers WHERE key = '_orgid_backfill_done'"
    )).first()
    if done and done[0] == "1":
        return

    for _tbl in ("sessions", "events", "event_flags"):
        conn.execute(
            sql_text(f"UPDATE {_tbl} SET org_id = :org WHERE org_id IS NULL"),
            {"org": DEFAULT_ORG_ID},
        )

    conn.execute(sql_text(
        "INSERT OR REPLACE INTO _schema_markers (key, value) VALUES ('_orgid_backfill_done', '1')"
    ))
