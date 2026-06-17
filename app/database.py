import os
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
import psycopg2
import psycopg2.extras
import pymssql

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://inventory:inventory@db:5432/inventory_sync"
)

LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", "14"))

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stores (
    id SERIAL PRIMARY KEY,
    store_name VARCHAR(100) NOT NULL,
    store_url VARCHAR(255) NOT NULL,
    admin_access_token VARCHAR(255) NOT NULL,
    publication_id VARCHAR(255),
    sync_enabled BOOLEAN DEFAULT FALSE,
    sync_interval_hours INTEGER DEFAULT 6,
    last_sync_at TIMESTAMPTZ,
    next_sync_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS store_locations (
    id SERIAL PRIMARY KEY,
    store_id INTEGER REFERENCES stores(id) ON DELETE CASCADE,
    location_id VARCHAR(255) NOT NULL,
    location_name VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sql_config (
    id SERIAL PRIMARY KEY,
    config_key VARCHAR(50) UNIQUE NOT NULL,
    host VARCHAR(255) NOT NULL,
    port INTEGER DEFAULT 1433,
    database_name VARCHAR(255) NOT NULL,
    username VARCHAR(255) NOT NULL,
    password VARCHAR(255) NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id SERIAL PRIMARY KEY,
    store_id INTEGER REFERENCES stores(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'running',
    total_products INTEGER DEFAULT 0,
    products_updated INTEGER DEFAULT 0,
    products_published INTEGER DEFAULT 0,
    products_unpublished INTEGER DEFAULT 0,
    products_skip_unpublish INTEGER DEFAULT 0,
    products_discontinued INTEGER DEFAULT 0,
    products_skipped INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    error_message TEXT,
    duration_seconds REAL
);

CREATE TABLE IF NOT EXISTS product_logs (
    id SERIAL PRIMARY KEY,
    sync_run_id INTEGER REFERENCES sync_runs(id) ON DELETE CASCADE,
    store_id INTEGER REFERENCES stores(id) ON DELETE CASCADE,
    product_upc VARCHAR(50) NOT NULL,
    product_description VARCHAR(255),
    shopify_variant_id VARCHAR(255),
    shopify_product_id VARCHAR(255),
    old_quantity INTEGER,
    new_quantity INTEGER,
    quantity_on_hand REAL,
    pending_po_quantity REAL,
    in_progress_quantity INTEGER,
    committed_quantity INTEGER,
    action VARCHAR(50),
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_store ON sync_runs(store_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_product_logs_run ON product_logs(sync_run_id);
CREATE INDEX IF NOT EXISTS idx_product_logs_upc ON product_logs(product_upc);
CREATE INDEX IF NOT EXISTS idx_product_logs_action_created ON product_logs(action, created_at);
CREATE INDEX IF NOT EXISTS idx_product_logs_store_created ON product_logs(store_id, created_at);

CREATE TABLE IF NOT EXISTS excluded_products (
    id SERIAL PRIMARY KEY,
    product_upc VARCHAR(50) UNIQUE NOT NULL,
    product_description VARCHAR(255),
    reason VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_excluded_products_upc ON excluded_products(product_upc);
"""


class PostgresManager:
    def __init__(self, database_url=None):
        self.database_url = database_url or DATABASE_URL

    def get_conn(self):
        return psycopg2.connect(self.database_url)

    def init_tables(self):
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(SCHEMA_SQL)
                cur.execute(
                    "ALTER TABLE product_logs ALTER COLUMN product_upc TYPE VARCHAR(50)"
                )
                cur.execute(
                    "ALTER TABLE sync_runs ADD COLUMN IF NOT EXISTS products_skip_unpublish INTEGER DEFAULT 0"
                )
                cur.execute(
                    "ALTER TABLE sync_runs ADD COLUMN IF NOT EXISTS products_discontinued INTEGER DEFAULT 0"
                )
                cur.execute(
                    "ALTER TABLE sync_runs ADD COLUMN IF NOT EXISTS products_excluded INTEGER DEFAULT 0"
                )
                # OAuth client credentials support
                cur.execute(
                    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS auth_method VARCHAR(40) DEFAULT 'legacy'"
                )
                cur.execute(
                    "ALTER TABLE stores ALTER COLUMN auth_method TYPE VARCHAR(40)"
                )
                cur.execute(
                    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS oauth_client_id VARCHAR(255)"
                )
                cur.execute(
                    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS oauth_client_secret VARCHAR(255)"
                )
                cur.execute(
                    "ALTER TABLE stores ALTER COLUMN admin_access_token DROP NOT NULL"
                )
                cur.execute(
                    "ALTER TABLE product_logs ADD COLUMN IF NOT EXISTS committed_quantity INTEGER"
                )
            conn.commit()
        logger.info("PostgreSQL tables initialized")
        # Build logs-performance indexes off the startup path. CREATE INDEX
        # CONCURRENTLY on a large product_logs table can take minutes and must
        # not block (or crash) app startup, so it runs best-effort in the
        # background; the app serves immediately and queries speed up once each
        # index is ready.
        threading.Thread(target=self._build_log_indexes, daemon=True).start()

    # Each entry is best-effort: a failure (e.g. pg_trgm needing superuser) is
    # logged and skipped without affecting the others or the running app.
    LOG_INDEX_DDL = (
        ("idx_product_logs_created",
         "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_product_logs_created "
         "ON product_logs (created_at DESC)"),
        ("pg_trgm", "CREATE EXTENSION IF NOT EXISTS pg_trgm"),
        ("idx_product_logs_upc_trgm",
         "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_product_logs_upc_trgm "
         "ON product_logs USING gin (product_upc gin_trgm_ops)"),
    )

    def _build_log_indexes(self):
        # Autocommit: CREATE INDEX CONCURRENTLY cannot run inside a transaction.
        conn = self.get_conn()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                for name, ddl in self.LOG_INDEX_DDL:
                    try:
                        cur.execute(ddl)
                        logger.info("Built logs index/extension: %s", name)
                    except Exception as exc:
                        logger.warning("Skipped logs index/extension %s: %s", name, exc)
                        # A failed CONCURRENTLY build can leave an INVALID index
                        # that IF NOT EXISTS would later skip; drop it so a retry
                        # can rebuild cleanly. Extensions have no index to drop.
                        if name.startswith("idx_"):
                            try:
                                cur.execute(f"DROP INDEX IF EXISTS {name}")
                            except Exception:
                                pass
        finally:
            conn.close()

    def purge_old_product_logs(
        self, retention_days=LOG_RETENTION_DAYS, batch_size=10000, stop_event=None
    ):
        """Delete product_logs older than retention_days in committed batches.
        Batching avoids a long lock, a huge WAL spike, and severe bloat that a
        single multi-million-row DELETE would cause on a large table. The inner
        lookup uses idx_product_logs_created. Returns total rows deleted."""
        conn = self.get_conn()
        conn.autocommit = True
        total = 0
        try:
            with conn.cursor() as cur:
                while stop_event is None or not stop_event.is_set():
                    cur.execute(
                        "DELETE FROM product_logs WHERE ctid IN ("
                        "SELECT ctid FROM product_logs "
                        "WHERE created_at < NOW() - make_interval(days => %s) "
                        "LIMIT %s)",
                        (retention_days, batch_size),
                    )
                    total += cur.rowcount
                    if cur.rowcount < batch_size:
                        break
                    # Ease off between batches so the initial large cleanup does
                    # not saturate disk I/O; interruptible when a stop_event is given.
                    if stop_event is not None:
                        stop_event.wait(0.5)
                    else:
                        time.sleep(0.5)
        finally:
            conn.close()
        return total

    # --- Stores ---

    def get_stores(self):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, store_name, store_url, publication_id, "
                    "auth_method, sync_enabled, sync_interval_hours, last_sync_at, "
                    "next_sync_at, created_at, updated_at FROM stores ORDER BY id"
                )
                return [dict(r) for r in cur.fetchall()]

    def get_store(self, store_id):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM stores WHERE id = %s", (store_id,))
                row = cur.fetchone()
                return dict(row) if row else None

    def create_store(self, data):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO stores (store_name, store_url, admin_access_token, "
                    "auth_method, oauth_client_id, oauth_client_secret, "
                    "sync_interval_hours) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                    (
                        data["store_name"],
                        data["store_url"],
                        data.get("admin_access_token"),
                        data.get("auth_method", "legacy"),
                        data.get("oauth_client_id"),
                        data.get("oauth_client_secret"),
                        data.get("sync_interval_hours", 6),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)

    def update_store(self, store_id, data):
        fields = []
        values = []
        for key in [
            "store_name",
            "store_url",
            "admin_access_token",
            "auth_method",
            "oauth_client_id",
            "oauth_client_secret",
            "publication_id",
            "sync_enabled",
            "sync_interval_hours",
            "last_sync_at",
            "next_sync_at",
        ]:
            if key in data:
                fields.append(f"{key} = %s")
                values.append(data[key])
        if not fields:
            return self.get_store(store_id)
        fields.append("updated_at = NOW()")
        values.append(store_id)
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"UPDATE stores SET {', '.join(fields)} WHERE id = %s RETURNING *",
                    values,
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None

    def delete_store(self, store_id):
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM stores WHERE id = %s", (store_id,))
                deleted = cur.rowcount
            conn.commit()
            return deleted > 0

    # --- Store Locations ---

    def get_store_locations(self, store_id):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM store_locations WHERE store_id = %s ORDER BY id",
                    (store_id,),
                )
                return [dict(r) for r in cur.fetchall()]

    def save_store_locations(self, store_id, locations):
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM store_locations WHERE store_id = %s", (store_id,)
                )
                for loc in locations:
                    cur.execute(
                        "INSERT INTO store_locations (store_id, location_id, location_name, is_active) "
                        "VALUES (%s, %s, %s, %s)",
                        (
                            store_id,
                            loc["location_id"],
                            loc.get("location_name", ""),
                            loc.get("is_active", True),
                        ),
                    )
            conn.commit()

    def get_active_location(self, store_id):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM store_locations WHERE store_id = %s AND is_active = TRUE LIMIT 1",
                    (store_id,),
                )
                row = cur.fetchone()
                return dict(row) if row else None

    # --- SQL Config ---

    def get_sql_configs(self, include_password=False):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if include_password:
                    cur.execute("SELECT * FROM sql_config ORDER BY config_key")
                else:
                    cur.execute(
                        "SELECT id, config_key, host, port, database_name, username, updated_at "
                        "FROM sql_config ORDER BY config_key"
                    )
                return [dict(r) for r in cur.fetchall()]

    def get_sql_config(self, config_key, include_password=True):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM sql_config WHERE config_key = %s", (config_key,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                result = dict(row)
                if not include_password:
                    result.pop("password", None)
                return result

    def save_sql_config(self, config_key, data):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO sql_config (config_key, host, port, database_name, username, password) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (config_key) DO UPDATE SET "
                    "host = EXCLUDED.host, port = EXCLUDED.port, "
                    "database_name = EXCLUDED.database_name, username = EXCLUDED.username, "
                    "password = EXCLUDED.password, updated_at = NOW() "
                    "RETURNING id, config_key, host, port, database_name, username, updated_at",
                    (
                        config_key,
                        data["host"],
                        data.get("port", 1433),
                        data["database_name"],
                        data["username"],
                        data["password"],
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)

    # --- Sync Runs ---

    def create_sync_run(self, store_id, started_at=None):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if started_at:
                    cur.execute(
                        "INSERT INTO sync_runs (store_id, started_at) VALUES (%s, %s) RETURNING *",
                        (store_id, started_at),
                    )
                else:
                    cur.execute(
                        "INSERT INTO sync_runs (store_id) VALUES (%s) RETURNING *",
                        (store_id,),
                    )
                row = cur.fetchone()
            conn.commit()
            return dict(row)

    def update_sync_run(self, run_id, data):
        fields = []
        values = []
        for key in [
            "finished_at",
            "status",
            "total_products",
            "products_updated",
            "products_published",
            "products_unpublished",
            "products_skip_unpublish",
            "products_discontinued",
            "products_excluded",
            "products_skipped",
            "errors_count",
            "error_message",
            "duration_seconds",
        ]:
            if key in data:
                fields.append(f"{key} = %s")
                values.append(data[key])
        if not fields:
            return
        values.append(run_id)
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE sync_runs SET {', '.join(fields)} WHERE id = %s", values
                )
            conn.commit()

    def has_running_sync(self, store_id):
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sync_runs SET status = 'failed', "
                    "error_message = 'Timed out (exceeded 15 minutes)', "
                    "finished_at = NOW() "
                    "WHERE store_id = %s AND status = 'running' "
                    "AND started_at < NOW() - INTERVAL '15 minutes'",
                    (store_id,),
                )
                timed_out = cur.rowcount
            conn.commit()
            if timed_out > 0:
                logger.info("Auto-timed out %d stuck sync(s) for store %s", timed_out, store_id)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM sync_runs WHERE store_id = %s AND status = 'running'",
                    (store_id,),
                )
                return cur.fetchone()[0] > 0

    def cancel_stuck_syncs(self, store_id):
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sync_runs SET status = 'failed', "
                    "error_message = 'Manually cancelled', "
                    "finished_at = NOW() "
                    "WHERE store_id = %s AND status = 'running'",
                    (store_id,),
                )
                cancelled = cur.rowcount
            conn.commit()
            if cancelled > 0:
                logger.info("Cancelled %d stuck sync(s) for store %s", cancelled, store_id)
            return cancelled

    def get_sync_runs(self, store_id=None, status=None, limit=50, offset=0):
        query = (
            "SELECT sr.*, s.store_name FROM sync_runs sr "
            "JOIN stores s ON sr.store_id = s.id WHERE 1=1"
        )
        params = []
        if store_id:
            query += " AND sr.store_id = %s"
            params.append(store_id)
        if status:
            query += " AND sr.status = %s"
            params.append(status)
        query += " ORDER BY sr.started_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]

    def delete_sync_run(self, run_id):
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sync_runs WHERE id = %s", (run_id,))
                deleted = cur.rowcount
            conn.commit()
            return deleted > 0

    # --- Product Logs ---

    def create_product_log(self, data):
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO product_logs (sync_run_id, store_id, product_upc, "
                    "product_description, shopify_variant_id, shopify_product_id, "
                    "old_quantity, new_quantity, quantity_on_hand, pending_po_quantity, "
                    "in_progress_quantity, committed_quantity, action, error_message) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        data.get("sync_run_id"),
                        data.get("store_id"),
                        data["product_upc"],
                        data.get("product_description"),
                        data.get("shopify_variant_id"),
                        data.get("shopify_product_id"),
                        data.get("old_quantity"),
                        data.get("new_quantity"),
                        data.get("quantity_on_hand"),
                        data.get("pending_po_quantity"),
                        data.get("in_progress_quantity"),
                        data.get("committed_quantity"),
                        data.get("action", "skip"),
                        data.get("error_message"),
                    ),
                )
            conn.commit()

    def create_product_logs_batch(self, logs):
        if not logs:
            return
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_batch(
                    cur,
                    "INSERT INTO product_logs (sync_run_id, store_id, product_upc, "
                    "product_description, shopify_variant_id, shopify_product_id, "
                    "old_quantity, new_quantity, quantity_on_hand, pending_po_quantity, "
                    "in_progress_quantity, committed_quantity, action, error_message, created_at) "
                    "VALUES (%(sync_run_id)s,%(store_id)s,%(product_upc)s,"
                    "%(product_description)s,%(shopify_variant_id)s,%(shopify_product_id)s,"
                    "%(old_quantity)s,%(new_quantity)s,%(quantity_on_hand)s,%(pending_po_quantity)s,"
                    "%(in_progress_quantity)s,%(committed_quantity)s,%(action)s,%(error_message)s,%(created_at)s)",
                    logs,
                )
            conn.commit()

    @staticmethod
    def _product_logs_filter(store_id=None, sync_run_id=None, upc=None, action=None):
        clause = " WHERE 1=1"
        params = []
        if store_id:
            clause += " AND pl.store_id = %s"
            params.append(store_id)
        if sync_run_id:
            clause += " AND pl.sync_run_id = %s"
            params.append(sync_run_id)
        if upc:
            clause += " AND pl.product_upc ILIKE %s"
            params.append(f"%{upc}%")
        if action:
            clause += " AND pl.action = %s"
            params.append(action)
        return clause, params

    def get_product_logs(
        self, store_id=None, sync_run_id=None, upc=None, action=None, limit=100, offset=0
    ):
        clause, params = self._product_logs_filter(store_id, sync_run_id, upc, action)
        query = (
            "SELECT pl.*, s.store_name FROM product_logs pl "
            "JOIN stores s ON pl.store_id = s.id" + clause
        )
        query += " ORDER BY pl.created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]

    def count_product_logs(self, store_id=None, sync_run_id=None, upc=None, action=None):
        """Return (total, estimated). The unfiltered total uses the planner's
        row estimate (O(1)) since an exact COUNT(*) over the whole table is an
        O(n) scan; filtered counts stay exact over their smaller subset."""
        if not any([store_id, sync_run_id, upc, action]):
            with self.get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT reltuples::bigint FROM pg_class WHERE relname = 'product_logs'"
                    )
                    row = cur.fetchone()
            return max(int(row[0]) if row and row[0] is not None else 0, 0), True
        clause, params = self._product_logs_filter(store_id, sync_run_id, upc, action)
        query = "SELECT COUNT(*) FROM product_logs pl" + clause
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                return cur.fetchone()[0], False

    # --- Dashboard Stats ---

    def get_dashboard_stats(self):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT COUNT(*) as total_stores FROM stores")
                total_stores = cur.fetchone()["total_stores"]

                cur.execute(
                    "SELECT COUNT(*) as total FROM sync_runs WHERE status = 'running'"
                )
                running_syncs = cur.fetchone()["total"]

                cur.execute(
                    "SELECT COUNT(*) as total FROM sync_runs WHERE started_at > NOW() - INTERVAL '24 hours'"
                )
                syncs_24h = cur.fetchone()["total"]

                cur.execute(
                    "SELECT COUNT(*) as total FROM product_logs WHERE created_at > NOW() - INTERVAL '24 hours'"
                )
                changes_24h = cur.fetchone()["total"]

                cur.execute(
                    "SELECT s.id AS store_id, s.store_name, s.sync_enabled, "
                    "sr.id AS run_id, sr.started_at, sr.status, "
                    "sr.total_products, sr.products_updated, sr.products_published, "
                    "sr.errors_count, sr.duration_seconds "
                    "FROM stores s "
                    "LEFT JOIN LATERAL ("
                    "  SELECT * FROM sync_runs r "
                    "  WHERE r.store_id = s.id "
                    "  ORDER BY r.started_at DESC LIMIT 1"
                    ") sr ON true "
                    "ORDER BY sr.started_at DESC NULLS LAST, s.store_name ASC"
                )
                recent_runs = [dict(r) for r in cur.fetchall()]

                return {
                    "total_stores": total_stores,
                    "running_syncs": running_syncs,
                    "syncs_24h": syncs_24h,
                    "changes_24h": changes_24h,
                    "recent_runs": recent_runs,
                }


    # --- Excluded Products ---

    def get_excluded_products(self):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM excluded_products ORDER BY created_at DESC")
                return [dict(r) for r in cur.fetchall()]

    def add_excluded_product(self, upc, description=None, reason=None):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO excluded_products (product_upc, product_description, reason) "
                    "VALUES (%s, %s, %s) ON CONFLICT (product_upc) DO NOTHING RETURNING *",
                    (upc, description, reason),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row) if row else None

    def delete_excluded_product(self, product_id):
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM excluded_products WHERE id = %s", (product_id,))
                deleted = cur.rowcount
            conn.commit()
            return deleted > 0

    def get_excluded_upcs_set(self):
        with self.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT product_upc FROM excluded_products")
                return {row[0] for row in cur.fetchall()}

    # --- Analytics ---

    def _analytics_cutoff(self, range_hours):
        return datetime.now(timezone.utc) - timedelta(hours=range_hours)

    def get_analytics_summary(self, store_id=None, range_hours=168):
        cutoff = self._analytics_cutoff(range_hours)
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                store_filter = "AND store_id = %s" if store_id else ""
                params_sr = [cutoff, store_id] if store_id else [cutoff]

                cur.execute(
                    f"SELECT "
                    f"COUNT(*) as total_syncs, "
                    f"COUNT(*) FILTER (WHERE status = 'success') as successful_syncs, "
                    f"COUNT(*) FILTER (WHERE status = 'failed') as failed_syncs, "
                    f"COUNT(*) FILTER (WHERE status = 'partial') as partial_syncs "
                    f"FROM sync_runs WHERE started_at >= %s {store_filter}",
                    params_sr,
                )
                sync_stats = dict(cur.fetchone())

                params_pl = [cutoff, store_id] if store_id else [cutoff]
                cur.execute(
                    f"SELECT "
                    f"COUNT(*) FILTER (WHERE action = 'inventory_update') as products_updated, "
                    f"COUNT(*) FILTER (WHERE action = 'republish') as products_published, "
                    f"COUNT(*) FILTER (WHERE action = 'unpublish') as products_unpublished, "
                    f"COUNT(*) FILTER (WHERE action = 'error') as errors_count, "
                    f"COUNT(*) as total_actions "
                    f"FROM product_logs WHERE created_at >= %s {store_filter}",
                    params_pl,
                )
                log_stats = dict(cur.fetchone())

                total = sync_stats["total_syncs"]
                success_rate = round(sync_stats["successful_syncs"] / total * 100, 1) if total > 0 else 0
                total_actions = log_stats["total_actions"]
                error_rate = round(log_stats["errors_count"] / total_actions * 100, 1) if total_actions > 0 else 0

                return {**sync_stats, **log_stats, "success_rate": success_rate, "error_rate": error_rate}

    def get_analytics_stock_trend(self, store_id=None, range_hours=168):
        cutoff = self._analytics_cutoff(range_hours)
        store_filter = "AND store_id = %s" if store_id else ""
        params = [cutoff, store_id] if store_id else [cutoff]
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT DATE(created_at) as day, "
                    f"COUNT(*) FILTER (WHERE action = 'republish') as published, "
                    f"COUNT(*) FILTER (WHERE action = 'unpublish') as unpublished "
                    f"FROM product_logs "
                    f"WHERE action IN ('republish', 'unpublish') AND created_at >= %s {store_filter} "
                    f"GROUP BY DATE(created_at) ORDER BY day",
                    params,
                )
                return [dict(r) for r in cur.fetchall()]

    def get_analytics_sync_activity(self, store_id=None, range_hours=168):
        cutoff = self._analytics_cutoff(range_hours)
        store_filter = "AND store_id = %s" if store_id else ""
        params = [cutoff, store_id] if store_id else [cutoff]
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT DATE(started_at) as day, "
                    f"SUM(COALESCE(products_updated, 0)) as updated, "
                    f"SUM(COALESCE(products_published, 0)) as published, "
                    f"SUM(COALESCE(products_unpublished, 0)) as unpublished, "
                    f"SUM(COALESCE(products_skipped, 0)) as skipped, "
                    f"SUM(COALESCE(errors_count, 0)) as errors "
                    f"FROM sync_runs WHERE started_at >= %s {store_filter} "
                    f"GROUP BY DATE(started_at) ORDER BY day",
                    params,
                )
                daily_activity = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"SELECT id, started_at, duration_seconds, status "
                    f"FROM sync_runs WHERE started_at >= %s AND duration_seconds IS NOT NULL {store_filter} "
                    f"ORDER BY started_at",
                    params,
                )
                duration_trend = [dict(r) for r in cur.fetchall()]

                return {"daily_activity": daily_activity, "duration_trend": duration_trend}

    def get_analytics_action_distribution(self, store_id=None, range_hours=168):
        cutoff = self._analytics_cutoff(range_hours)
        store_filter = "AND store_id = %s" if store_id else ""
        params = [cutoff, store_id] if store_id else [cutoff]
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT action, COUNT(*) as count "
                    f"FROM product_logs WHERE created_at >= %s {store_filter} "
                    f"GROUP BY action ORDER BY count DESC",
                    params,
                )
                return [dict(r) for r in cur.fetchall()]

    def get_analytics_top_movers(self, store_id=None, range_hours=168, limit=20):
        cutoff = self._analytics_cutoff(range_hours)
        store_filter = "AND store_id = %s" if store_id else ""
        params = [cutoff, store_id, limit] if store_id else [cutoff, limit]
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT product_upc, product_description, "
                    f"SUM(ABS(COALESCE(new_quantity, 0) - COALESCE(old_quantity, 0))) as total_change, "
                    f"COUNT(*) as update_count "
                    f"FROM product_logs "
                    f"WHERE action = 'inventory_update' AND created_at >= %s {store_filter} "
                    f"GROUP BY product_upc, product_description "
                    f"ORDER BY total_change DESC LIMIT %s",
                    params,
                )
                top_movers = [dict(r) for r in cur.fetchall()]

                cur.execute(
                    f"SELECT product_upc, product_description, COUNT(*) as oos_count "
                    f"FROM product_logs "
                    f"WHERE action = 'unpublish' AND created_at >= %s {store_filter} "
                    f"GROUP BY product_upc, product_description "
                    f"ORDER BY oos_count DESC LIMIT %s",
                    params,
                )
                frequent_oos = [dict(r) for r in cur.fetchall()]

                return {"top_movers": top_movers, "frequent_oos": frequent_oos}

    def get_analytics_errors(self, store_id=None, range_hours=168, limit=20):
        cutoff = self._analytics_cutoff(range_hours)
        store_filter = "AND pl.store_id = %s" if store_id else ""
        params_recent = [cutoff, store_id, limit] if store_id else [cutoff, limit]
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT pl.product_upc, pl.product_description, pl.error_message, "
                    f"pl.created_at, s.store_name "
                    f"FROM product_logs pl JOIN stores s ON pl.store_id = s.id "
                    f"WHERE pl.action = 'error' AND pl.created_at >= %s {store_filter} "
                    f"ORDER BY pl.created_at DESC LIMIT %s",
                    params_recent,
                )
                recent_errors = [dict(r) for r in cur.fetchall()]

                store_filter_pl = "AND store_id = %s" if store_id else ""
                params_trend = [cutoff, store_id] if store_id else [cutoff]
                cur.execute(
                    f"SELECT DATE(created_at) as day, "
                    f"COUNT(*) FILTER (WHERE action = 'error') as errors, "
                    f"COUNT(*) as total, "
                    f"CASE WHEN COUNT(*) > 0 "
                    f"THEN ROUND(COUNT(*) FILTER (WHERE action = 'error')::numeric / COUNT(*) * 100, 1) "
                    f"ELSE 0 END as error_rate "
                    f"FROM product_logs WHERE created_at >= %s {store_filter_pl} "
                    f"GROUP BY DATE(created_at) ORDER BY day",
                    params_trend,
                )
                error_trend = [dict(r) for r in cur.fetchall()]

                return {"recent_errors": recent_errors, "error_trend": error_trend}


class MSSQLManager:
    def __init__(self, host, port, database, username, password):
        self.host = host
        self.port = port
        self.database = database
        self.username = username
        self.password = password

    def get_conn(self):
        return pymssql.connect(
            server=self.host,
            port=self.port,
            user=self.username,
            password=self.password,
            database=self.database,
        )

    def test_connection(self):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        return True

    def get_server_time(self):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT GETUTCDATE()")
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row[0]

    def get_on_hand_quantities(self):
        conn = self.get_conn()
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT ProductUPC, ISNULL(QuantOnHand, 0) as QuantOnHand "
            "FROM Items_tbl WHERE ProductUPC IS NOT NULL AND ProductUPC != ''"
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return {row["ProductUPC"]: float(row["QuantOnHand"]) for row in rows}

    def get_pending_po_quantities(self):
        conn = self.get_conn()
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT ProductUPC, SUM(ISNULL(QtyOrdered, 0) - ISNULL(QtyReceived, 0)) as PendingQty "
            "FROM PurchaseOrdersDetails_tbl "
            "WHERE Committedln = 0 AND ProductUPC IS NOT NULL AND ProductUPC != '' "
            "GROUP BY ProductUPC"
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return {row["ProductUPC"]: float(row["PendingQty"]) for row in rows}

    def get_in_progress_quantities(self):
        conn = self.get_conn()
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT ProductUPC, SUM(ISNULL(Qty, 0)) as InProgressQty "
            "FROM QuotationsInProgress "
            "WHERE ProductUPC IS NOT NULL AND ProductUPC != '' "
            "AND (SourceDB IS NULL OR SourceDB NOT LIKE '%webSites%') "
            "GROUP BY ProductUPC"
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return {row["ProductUPC"]: int(row["InProgressQty"]) for row in rows}

    def search_products(self, query, limit=20):
        conn = self.get_conn()
        cursor = conn.cursor(as_dict=True)
        like_query = f"%{query}%"
        cursor.execute(
            "SELECT TOP %d ProductUPC, ProductDescription "
            "FROM Items_tbl "
            "WHERE (ProductUPC LIKE %s OR ProductDescription LIKE %s) "
            "AND ProductUPC IS NOT NULL AND ProductUPC != ''",
            (limit, like_query, like_query),
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return [{"upc": row["ProductUPC"], "description": row["ProductDescription"]} for row in rows]

    def get_discontinued_barcodes(self):
        conn = self.get_conn()
        cursor = conn.cursor(as_dict=True)
        cursor.execute(
            "SELECT ProductUPC "
            "FROM Items_tbl "
            "WHERE ProductUPC IS NOT NULL AND ProductUPC != '' "
            "AND Discontinued = 1"
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return {row["ProductUPC"] for row in rows}
