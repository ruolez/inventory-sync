import os
import logging
import threading
from flask import Flask, jsonify, request, render_template

from app.database import PostgresManager, MSSQLManager
from app.shopify_client import ShopifyClient
from app.sync_engine import run_sync
from app import scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
pg = PostgresManager()


@app.after_request
def add_no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def json_serial(obj):
    from datetime import datetime, date
    from decimal import Decimal

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


def to_json(data, status=200):
    import json

    return app.response_class(
        json.dumps(data, default=json_serial),
        mimetype="application/json",
        status=status,
    )


# --- Page Routes ---


@app.route("/")
def dashboard_page():
    return render_template("dashboard.html")


@app.route("/stores")
def stores_page():
    return render_template("stores.html")


@app.route("/sql-config")
def sql_config_page():
    return render_template("sql-config.html")


@app.route("/sync-history")
def sync_history_page():
    return render_template("sync-history.html")


@app.route("/logs")
def logs_page():
    return render_template("logs.html")


@app.route("/analytics")
def analytics_page():
    return render_template("analytics.html")


# --- Health ---


@app.route("/health")
def health():
    try:
        conn = pg.get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return jsonify({"status": "healthy", "database": "connected"})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500


# --- Store API ---


@app.route("/api/stores", methods=["GET"])
def get_stores():
    stores = pg.get_stores()
    return to_json(stores)


@app.route("/api/stores", methods=["POST"])
def create_store():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    for field in ["store_name", "store_url", "admin_access_token"]:
        if not data.get(field):
            return jsonify({"error": f"{field} is required"}), 400
    store = pg.create_store(data)
    return to_json(store, 201)


@app.route("/api/stores/<int:store_id>", methods=["PUT"])
def update_store(store_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    store = pg.update_store(store_id, data)
    if not store:
        return jsonify({"error": "Store not found"}), 404
    return to_json(store)


@app.route("/api/stores/<int:store_id>", methods=["DELETE"])
def delete_store(store_id):
    scheduler.stop_scheduler(store_id, pg)
    if pg.delete_store(store_id):
        return jsonify({"success": True})
    return jsonify({"error": "Store not found"}), 404


@app.route("/api/stores/<int:store_id>/test", methods=["POST"])
def test_store_connection(store_id):
    store = pg.get_store(store_id)
    if not store:
        return jsonify({"error": "Store not found"}), 404
    try:
        client = ShopifyClient(store["store_url"], store["admin_access_token"])
        result = client.test_connection()
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/stores/<int:store_id>/locations", methods=["GET"])
def get_store_locations(store_id):
    store = pg.get_store(store_id)
    if not store:
        return jsonify({"error": "Store not found"}), 404
    try:
        client = ShopifyClient(store["store_url"], store["admin_access_token"])
        locations = client.get_locations()
        saved = pg.get_store_locations(store_id)
        saved_map = {loc["location_id"]: loc for loc in saved}
        for loc in locations:
            saved_loc = saved_map.get(loc["id"])
            loc["is_saved"] = saved_loc is not None
            loc["is_active_saved"] = saved_loc["is_active"] if saved_loc else False
        return jsonify(locations)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/stores/<int:store_id>/locations", methods=["POST"])
def save_store_locations(store_id):
    data = request.get_json()
    if not data or "locations" not in data:
        return jsonify({"error": "locations array required"}), 400
    pg.save_store_locations(store_id, data["locations"])
    return jsonify({"success": True})


@app.route("/api/stores/<int:store_id>/publication", methods=["POST"])
def fetch_publication(store_id):
    store = pg.get_store(store_id)
    if not store:
        return jsonify({"error": "Store not found"}), 404
    try:
        client = ShopifyClient(store["store_url"], store["admin_access_token"])
        pub = client.get_online_store_publication()
        if pub:
            pg.update_store(store_id, {"publication_id": pub["id"]})
            return jsonify({"success": True, "publication": pub})
        return jsonify({"error": "No Online Store publication found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# --- SQL Config API ---


@app.route("/api/config/sql", methods=["GET"])
def get_sql_configs():
    configs = pg.get_sql_configs(include_password=False)
    return to_json(configs)


@app.route("/api/config/sql", methods=["POST"])
def save_sql_config():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    config_key = data.get("config_key")
    if config_key not in ("s2s", "db_admin"):
        return jsonify({"error": "config_key must be 's2s' or 'db_admin'"}), 400
    for field in ["host", "database_name", "username", "password"]:
        if not data.get(field):
            return jsonify({"error": f"{field} is required"}), 400
    result = pg.save_sql_config(config_key, data)
    return to_json(result)


@app.route("/api/config/test-s2s", methods=["POST"])
def test_s2s_connection():
    return _test_sql_connection("s2s")


@app.route("/api/config/test-admin", methods=["POST"])
def test_admin_connection():
    return _test_sql_connection("db_admin")


def _test_sql_connection(config_key):
    config = pg.get_sql_config(config_key)
    if not config:
        return jsonify({"error": f"{config_key} not configured"}), 400
    try:
        mgr = MSSQLManager(
            config["host"],
            config["port"],
            config["database_name"],
            config["username"],
            config["password"],
        )
        mgr.test_connection()
        return jsonify({"success": True, "message": "Connection successful"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


# --- Sync API ---


@app.route("/api/sync/<int:store_id>/trigger", methods=["POST"])
def trigger_sync(store_id):
    store = pg.get_store(store_id)
    if not store:
        return jsonify({"error": "Store not found"}), 404
    if pg.has_running_sync(store_id):
        return jsonify({"error": "Sync already running for this store"}), 409

    def _run():
        run_sync(store_id, pg)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({"success": True, "message": "Sync started"})


@app.route("/api/sync/<int:store_id>/cancel", methods=["POST"])
def cancel_sync(store_id):
    store = pg.get_store(store_id)
    if not store:
        return jsonify({"error": "Store not found"}), 404
    cancelled = pg.cancel_stuck_syncs(store_id)
    return jsonify({"cancelled": cancelled})


@app.route("/api/sync/<int:store_id>/start", methods=["POST"])
def start_scheduler(store_id):
    store = pg.get_store(store_id)
    if not store:
        return jsonify({"error": "Store not found"}), 404
    interval = store.get("sync_interval_hours", 6)
    if scheduler.start_scheduler(store_id, interval, pg):
        return jsonify({"success": True, "message": "Scheduler started"})
    return jsonify({"error": "Scheduler already running"}), 409


@app.route("/api/sync/<int:store_id>/stop", methods=["POST"])
def stop_scheduler(store_id):
    if scheduler.stop_scheduler(store_id, pg):
        return jsonify({"success": True, "message": "Scheduler stopped"})
    return jsonify({"error": "Scheduler not running"}), 404


@app.route("/api/sync/status", methods=["GET"])
def sync_status():
    return jsonify(scheduler.get_all_statuses())


# --- History API ---


@app.route("/api/history", methods=["GET"])
def get_history():
    store_id = request.args.get("store_id", type=int)
    status = request.args.get("status")
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)
    runs = pg.get_sync_runs(store_id=store_id, status=status, limit=limit, offset=offset)
    return to_json(runs)


@app.route("/api/history/<int:run_id>", methods=["DELETE"])
def delete_history(run_id):
    if pg.delete_sync_run(run_id):
        return jsonify({"success": True})
    return jsonify({"error": "Run not found"}), 404


# --- Logs API ---


@app.route("/api/logs", methods=["GET"])
def get_logs():
    store_id = request.args.get("store_id", type=int)
    sync_run_id = request.args.get("sync_run_id", type=int)
    upc = request.args.get("upc")
    action = request.args.get("action")
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    logs = pg.get_product_logs(
        store_id=store_id,
        sync_run_id=sync_run_id,
        upc=upc,
        action=action,
        limit=limit,
        offset=offset,
    )
    return to_json(logs)


# --- Dashboard Stats ---


@app.route("/api/dashboard/stats", methods=["GET"])
def dashboard_stats():
    stats = pg.get_dashboard_stats()
    return to_json(stats)


def _parse_range(range_str):
    return {"24h": 24, "7d": 168, "30d": 720, "90d": 2160}.get(range_str, 168)


# --- Analytics API ---


@app.route("/api/analytics/summary", methods=["GET"])
def analytics_summary():
    store_id = request.args.get("store_id", type=int)
    range_hours = _parse_range(request.args.get("range", "7d"))
    data = pg.get_analytics_summary(store_id=store_id, range_hours=range_hours)
    return to_json(data)


@app.route("/api/analytics/stock-trend", methods=["GET"])
def analytics_stock_trend():
    store_id = request.args.get("store_id", type=int)
    range_hours = _parse_range(request.args.get("range", "7d"))
    data = pg.get_analytics_stock_trend(store_id=store_id, range_hours=range_hours)
    return to_json(data)


@app.route("/api/analytics/sync-activity", methods=["GET"])
def analytics_sync_activity():
    store_id = request.args.get("store_id", type=int)
    range_hours = _parse_range(request.args.get("range", "7d"))
    data = pg.get_analytics_sync_activity(store_id=store_id, range_hours=range_hours)
    return to_json(data)


@app.route("/api/analytics/action-distribution", methods=["GET"])
def analytics_action_distribution():
    store_id = request.args.get("store_id", type=int)
    range_hours = _parse_range(request.args.get("range", "7d"))
    data = pg.get_analytics_action_distribution(store_id=store_id, range_hours=range_hours)
    return to_json(data)


@app.route("/api/analytics/top-movers", methods=["GET"])
def analytics_top_movers():
    store_id = request.args.get("store_id", type=int)
    range_hours = _parse_range(request.args.get("range", "7d"))
    data = pg.get_analytics_top_movers(store_id=store_id, range_hours=range_hours)
    return to_json(data)


@app.route("/api/analytics/errors", methods=["GET"])
def analytics_errors():
    store_id = request.args.get("store_id", type=int)
    range_hours = _parse_range(request.args.get("range", "7d"))
    data = pg.get_analytics_errors(store_id=store_id, range_hours=range_hours)
    return to_json(data)


if __name__ == "__main__":
    pg.init_tables()
    for store in pg.get_stores():
        if store.get("sync_enabled"):
            interval = store.get("sync_interval_hours", 6)
            scheduler.start_scheduler(store["id"], interval, pg)
            logger.info("Restored scheduler for store %s (%s)", store["id"], store["store_name"])
    logger.info("Starting Inventory Sync on port 5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
