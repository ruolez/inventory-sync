import os
import logging
import psycopg2
import psycopg2.extras
import pymssql

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://inventory:inventory@db:5432/inventory_sync"
)

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
    products_skipped INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    error_message TEXT,
    duration_seconds REAL
);

CREATE TABLE IF NOT EXISTS product_logs (
    id SERIAL PRIMARY KEY,
    sync_run_id INTEGER REFERENCES sync_runs(id) ON DELETE CASCADE,
    store_id INTEGER REFERENCES stores(id) ON DELETE CASCADE,
    product_upc VARCHAR(20) NOT NULL,
    product_description VARCHAR(255),
    shopify_variant_id VARCHAR(255),
    shopify_product_id VARCHAR(255),
    old_quantity INTEGER,
    new_quantity INTEGER,
    quantity_on_hand REAL,
    pending_po_quantity REAL,
    in_progress_quantity INTEGER,
    action VARCHAR(50),
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_store ON sync_runs(store_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_product_logs_run ON product_logs(sync_run_id);
CREATE INDEX IF NOT EXISTS idx_product_logs_upc ON product_logs(product_upc);
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
            conn.commit()
        logger.info("PostgreSQL tables initialized")

    # --- Stores ---

    def get_stores(self):
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, store_name, store_url, publication_id, "
                    "sync_enabled, sync_interval_hours, last_sync_at, next_sync_at, "
                    "created_at, updated_at FROM stores ORDER BY id"
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
                    "sync_interval_hours) VALUES (%s, %s, %s, %s) RETURNING *",
                    (
                        data["store_name"],
                        data["store_url"],
                        data["admin_access_token"],
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
                    "in_progress_quantity, action, error_message) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
                    "in_progress_quantity, action, error_message, created_at) "
                    "VALUES (%(sync_run_id)s,%(store_id)s,%(product_upc)s,"
                    "%(product_description)s,%(shopify_variant_id)s,%(shopify_product_id)s,"
                    "%(old_quantity)s,%(new_quantity)s,%(quantity_on_hand)s,%(pending_po_quantity)s,"
                    "%(in_progress_quantity)s,%(action)s,%(error_message)s,%(created_at)s)",
                    logs,
                )
            conn.commit()

    def get_product_logs(
        self, store_id=None, sync_run_id=None, upc=None, action=None, limit=100, offset=0
    ):
        query = (
            "SELECT pl.*, s.store_name FROM product_logs pl "
            "JOIN stores s ON pl.store_id = s.id WHERE 1=1"
        )
        params = []
        if store_id:
            query += " AND pl.store_id = %s"
            params.append(store_id)
        if sync_run_id:
            query += " AND pl.sync_run_id = %s"
            params.append(sync_run_id)
        if upc:
            query += " AND pl.product_upc ILIKE %s"
            params.append(f"%{upc}%")
        if action:
            query += " AND pl.action = %s"
            params.append(action)
        query += " ORDER BY pl.created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        with self.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]

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
                    "SELECT sr.*, s.store_name FROM sync_runs sr "
                    "JOIN stores s ON sr.store_id = s.id "
                    "ORDER BY sr.started_at DESC LIMIT 5"
                )
                recent_runs = [dict(r) for r in cur.fetchall()]

                return {
                    "total_stores": total_stores,
                    "running_syncs": running_syncs,
                    "syncs_24h": syncs_24h,
                    "changes_24h": changes_24h,
                    "recent_runs": recent_runs,
                }


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
            "GROUP BY ProductUPC"
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        return {row["ProductUPC"]: int(row["InProgressQty"]) for row in rows}
