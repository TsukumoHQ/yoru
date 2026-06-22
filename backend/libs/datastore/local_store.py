"""Schemaless document store backing the local (no-Supabase) data layer.

Every logical table is stored as JSON documents in one physical table
(``datastore_records``), keyed by ``(table_name, record_id)``. Filtering,
ordering and pagination happen in Python. At single-user self-host scale the
dataset is tiny, so this is plenty fast — and it means we never have to mirror
16 Postgres table schemas by hand.

Implements the exact CRUD surface of ``SupabaseManager`` so services are
backend-blind. ``access_token`` / ``use_anon_key`` constructor kwargs are
accepted and ignored (the local store is single-tenant — no RLS).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Field, Session, SQLModel

from apps.api.api.routers.receipt.db import engine
from libs.log_manager.controller import LoggingController


class DataRecord(SQLModel, table=True):
    """One JSON document. Composite PK (table_name, record_id)."""

    __tablename__ = "datastore_records"

    table_name: str = Field(primary_key=True)
    record_id: str = Field(primary_key=True)
    data: str  # JSON-encoded document


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def _matches(doc: dict, filters: dict[str, Any] | None) -> bool:
    """Apply the SupabaseManager filter grammar in Python.

    A filter value is either a scalar (``eq``), a list (``in``), or a dict of
    ``{op: value}`` where op ∈ eq/neq/gt/gte/lt/lte/in/any/like/ilike/is/contains.
    """
    if not filters:
        return True
    for column, cond in filters.items():
        actual = doc.get(column)
        if isinstance(cond, list):
            if actual not in cond:
                return False
        elif isinstance(cond, dict):
            for op, val in cond.items():
                if op in ("in", "any"):
                    if actual not in (val or []):
                        return False
                elif op == "eq":
                    if actual != val:
                        return False
                elif op == "neq":
                    if actual == val:
                        return False
                elif op == "gt":
                    if not (actual is not None and actual > val):
                        return False
                elif op == "gte":
                    if not (actual is not None and actual >= val):
                        return False
                elif op == "lt":
                    if not (actual is not None and actual < val):
                        return False
                elif op == "lte":
                    if not (actual is not None and actual <= val):
                        return False
                elif op in ("like", "ilike"):
                    pat = str(val).replace("%", "")
                    a = "" if actual is None else str(actual)
                    if op == "ilike":
                        if pat.lower() not in a.lower():
                            return False
                    elif pat not in a:
                        return False
                elif op == "is":
                    if val in ("null", None) and actual is not None:
                        return False
                elif op == "contains":
                    if not isinstance(actual, (list, str)) or val not in actual:
                        return False
                else:  # unknown op → treat as eq
                    if actual != val:
                        return False
        else:
            if actual != cond:
                return False
    return True


class _Response:
    """Mimics a postgrest APIResponse (only ``.data`` / ``.count`` are read)."""

    def __init__(self, data: Any, count: int | None = None) -> None:
        self.data = data
        self.count = count


class _QueryBuilder:
    """Minimal PostgREST-style fluent builder over the JSON document store.

    Covers the surface the codebase actually uses: select/insert/update/upsert/
    delete, eq/neq/in_/is_/gt(e)/lt(e)/like/ilike, order/limit/range,
    single/maybe_single, and ``select(count="exact")``.
    """

    def __init__(self, store: LocalDataStore, table: str) -> None:
        self._store = store
        self._table = table
        self._op = "select"
        self._payload: Any = None
        self._filters: dict[str, Any] = {}
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None
        self._offset: int | None = None
        self._count = False
        self._single = False
        self._single_optional = False

    # operations
    def select(self, *columns: str, count: str | None = None) -> _QueryBuilder:
        self._op = "select"
        self._count = count is not None
        return self

    def insert(self, data: Any) -> _QueryBuilder:
        self._op, self._payload = "insert", data
        return self

    def update(self, data: dict) -> _QueryBuilder:
        self._op, self._payload = "update", data
        return self

    def upsert(self, data: Any) -> _QueryBuilder:
        self._op, self._payload = "upsert", data
        return self

    def delete(self) -> _QueryBuilder:
        self._op = "delete"
        return self

    # filters
    def eq(self, col: str, val: Any) -> _QueryBuilder:
        self._filters[col] = val
        return self

    def neq(self, col: str, val: Any) -> _QueryBuilder:
        self._filters[col] = {"neq": val}
        return self

    def in_(self, col: str, vals: list) -> _QueryBuilder:
        self._filters[col] = list(vals)
        return self

    def match(self, criteria: dict) -> _QueryBuilder:
        """PostgREST .match({col: val, ...}) → equality on every pair."""
        for col, val in criteria.items():
            self._filters[col] = val
        return self

    def is_(self, col: str, val: Any) -> _QueryBuilder:
        self._filters[col] = {"is": val}
        return self

    def _set(self, col: str, op: str, val: Any) -> _QueryBuilder:
        self._filters[col] = {op: val}
        return self

    def gt(self, col, val):
        return self._set(col, "gt", val)

    def gte(self, col, val):
        return self._set(col, "gte", val)

    def lt(self, col, val):
        return self._set(col, "lt", val)

    def lte(self, col, val):
        return self._set(col, "lte", val)

    def like(self, col, val):
        return self._set(col, "like", val)

    def ilike(self, col, val):
        return self._set(col, "ilike", val)

    def order(self, col: str, desc: bool = False) -> _QueryBuilder:
        self._order = (col, desc)
        return self

    def limit(self, n: int) -> _QueryBuilder:
        self._limit = n
        return self

    def range(self, start: int, end: int) -> _QueryBuilder:
        self._offset = start
        self._limit = end - start + 1
        return self

    def single(self) -> _QueryBuilder:
        self._single = True
        return self

    def maybe_single(self) -> _QueryBuilder:
        self._single = True
        self._single_optional = True
        return self

    def execute(self) -> _Response:
        s = self._store
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = [s.insert_record(self._table, d) for d in payload]
            return _Response(out)
        if self._op == "upsert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            out = [s.upsert_record(self._table, d) for d in payload]
            return _Response(out)
        if self._op == "update":
            matches = s.query_records(self._table, filters=self._filters or None)
            out = [s.update_record(self._table, m["id"], self._payload) for m in matches if "id" in m]
            return _Response(out)
        if self._op == "delete":
            matches = s.query_records(self._table, filters=self._filters or None)
            for m in matches:
                if "id" in m:
                    s.delete_record(self._table, m["id"])
            return _Response(matches)
        # select
        order_by, desc = self._order or (None, False)
        total = s.count_records(self._table, filters=self._filters or None) if self._count else None
        rows = s.query_records(
            self._table,
            filters=self._filters or None,
            limit=self._limit,
            offset=self._offset,
            order_by=order_by,
            desc=desc,
        )
        if self._single:
            data = rows[0] if rows else None
            if data is None and not self._single_optional:
                # postgrest .single() raises on no rows; callers that need that
                # use maybe_single(). Return None defensively here.
                data = None
            return _Response(data, total)
        return _Response(rows, total)


class _ClientShim:
    """Stands in for ``SupabaseManager.client`` — only ``.table()`` is used for
    data (``.auth`` is handled by the auth provider, not here)."""

    def __init__(self, store: LocalDataStore) -> None:
        self._store = store

    def table(self, name: str) -> _QueryBuilder:
        return _QueryBuilder(self._store, name)


class LocalDataStore:
    """Drop-in replacement for SupabaseManager's data methods."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Accept and ignore SupabaseManager-style kwargs (access_token, etc.).
        self.logger = LoggingController(app_name="LocalDataStore")
        self.enable_cache = False
        self.cache = None
        self.client = _ClientShim(self)

    # ── raw helpers ──────────────────────────────────────────────────────
    def _rows(self, session: Session, table: str) -> list[tuple[str, dict]]:
        res = session.execute(
            text("SELECT record_id, data FROM datastore_records WHERE table_name = :t"),
            {"t": table},
        ).fetchall()
        return [(r[0], json.loads(r[1])) for r in res]

    def _write(self, session: Session, table: str, rid: str, doc: dict) -> None:
        session.execute(
            text(
                "INSERT INTO datastore_records (table_name, record_id, data) "
                "VALUES (:t, :i, :d) "
                "ON CONFLICT(table_name, record_id) DO UPDATE SET data = excluded.data"
            ),
            {"t": table, "i": rid, "d": json.dumps(doc, default=str)},
        )

    # ── CRUD surface (mirrors SupabaseManager) ───────────────────────────
    def insert_record(
        self, table: str, data: dict[str, Any], correlation_id: str | None = None
    ) -> dict[str, Any]:
        doc = dict(data)
        rid = str(doc.get("id") or uuid4())
        doc.setdefault("id", rid)
        doc.setdefault("created_at", _utcnow_iso())
        doc.setdefault("updated_at", _utcnow_iso())
        with Session(engine) as session:
            self._write(session, table, rid, doc)
            session.commit()
        return doc

    def get_record(
        self,
        table: str,
        record_id: Any,
        id_column: str = "id",
        correlation_id: str | None = None,
    ) -> dict[str, Any] | None:
        with Session(engine) as session:
            if id_column == "id":
                res = session.execute(
                    text(
                        "SELECT data FROM datastore_records "
                        "WHERE table_name = :t AND record_id = :i"
                    ),
                    {"t": table, "i": str(record_id)},
                ).fetchone()
                return json.loads(res[0]) if res else None
            for _, doc in self._rows(session, table):
                if doc.get(id_column) == record_id:
                    return doc
        return None

    def update_record(
        self,
        table: str,
        record_id: Any,
        data: dict[str, Any],
        id_column: str = "id",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        with Session(engine) as session:
            target_rid: str | None = None
            current: dict | None = None
            if id_column == "id":
                res = session.execute(
                    text(
                        "SELECT data FROM datastore_records "
                        "WHERE table_name = :t AND record_id = :i"
                    ),
                    {"t": table, "i": str(record_id)},
                ).fetchone()
                if res:
                    target_rid, current = str(record_id), json.loads(res[0])
            else:
                for rid, doc in self._rows(session, table):
                    if doc.get(id_column) == record_id:
                        target_rid, current = rid, doc
                        break
            if current is None:
                raise RuntimeError(
                    f"No record in '{table}' where {id_column}={record_id}"
                )
            current.update(data)
            current["updated_at"] = _utcnow_iso()
            self._write(session, table, target_rid, current)
            session.commit()
            return current

    def upsert_record(
        self,
        table: str,
        data: dict[str, Any],
        on_conflict: str | None = None,
        conflict_columns: list[str] | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        if data.get("id"):
            existing = self.get_record(table, data["id"], correlation_id=correlation_id)
            if existing:
                return self.update_record(
                    table, data["id"], data, correlation_id=correlation_id
                )
        return self.insert_record(table, data, correlation_id=correlation_id)

    def delete_record(
        self,
        table: str,
        record_id: Any,
        id_column: str = "id",
        correlation_id: str | None = None,
    ) -> bool:
        with Session(engine) as session:
            if id_column == "id":
                session.execute(
                    text(
                        "DELETE FROM datastore_records "
                        "WHERE table_name = :t AND record_id = :i"
                    ),
                    {"t": table, "i": str(record_id)},
                )
            else:
                for rid, doc in self._rows(session, table):
                    if doc.get(id_column) == record_id:
                        session.execute(
                            text(
                                "DELETE FROM datastore_records "
                                "WHERE table_name = :t AND record_id = :i"
                            ),
                            {"t": table, "i": rid},
                        )
            session.commit()
        return True

    def query_records(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        select_columns: str = "*",
        limit: int | None = None,
        offset: int | None = None,
        correlation_id: str | None = None,
        order_by: str | None = None,
        desc: bool = False,
    ) -> list[dict[str, Any]]:
        with Session(engine) as session:
            docs = [doc for _, doc in self._rows(session, table) if _matches(doc, filters)]
        if order_by:
            docs.sort(key=lambda d: (d.get(order_by) is None, d.get(order_by)), reverse=desc)
        if offset:
            docs = docs[offset:]
        if limit is not None:
            docs = docs[:limit]
        return docs

    def count_records(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> int:
        with Session(engine) as session:
            return sum(1 for _, doc in self._rows(session, table) if _matches(doc, filters))

    def count_records_cached(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        cache_ttl: int = 300,
    ) -> int:
        return self.count_records(table, filters, correlation_id)

    def execute_rpc(self, function_name: str, *args: Any, **kwargs: Any) -> Any:
        # No Postgres functions in this codebase; fail loudly if one appears.
        raise NotImplementedError(
            f"execute_rpc('{function_name}') is not supported by the local data store"
        )

    def invalidate_cache(self, *args: Any, **kwargs: Any) -> int:
        return 0
