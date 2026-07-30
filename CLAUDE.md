# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Shopify inventory sync application. Pulls inventory from two MS SQL Server databases (S2S for on-hand/PO quantities, DB_ADMIN for in-progress quantities), subtracts units committed to open orders aggregated across all stores, calculates final stock (`max(0, on_hand + confirmed_po - in_progress - committed)`), and pushes to Shopify via GraphQL Admin API. Also manages product publish/unpublish based on stock levels.

Stack: Python 3.11, Flask, PostgreSQL 16, Vanilla JS, Docker.

## Development Commands

```bash
# Start services (Flask on port 80, PostgreSQL on 5432)
docker compose up -d --build

# Follow application logs
docker compose logs -f web

# Restart after Python changes
docker compose restart web

# Stop everything
docker compose down
```

No test suite, linter, or CI pipeline exists.

## Architecture

### Sync Flow (4 phases in `sync_engine.py`)

1. **Fetch Shopify** — `shopify_client.get_all_variants(location_id)` queries `location.inventoryLevels` via GraphQL cursor pagination. Returns `{barcode: variant_data}` dict.
1B. **Aggregate committed across all stores** — Sums Shopify `committed` quantity per barcode across the current store *and every other configured store* (each at its active location), excluding units committed to archived/closed-but-unfulfilled orders. All stores draw from the same physical warehouse, so committed-but-unshipped units are reserved globally to prevent overselling.
2. **Fetch SQL Server** — Three queries across two MSSQL databases: on-hand (Items_tbl), pending PO (PurchaseOrdersDetails_tbl LEFT JOINed to PurchaseOrders_tbl), in-progress (QuotationsInProgress); plus discontinued barcodes. The PO query splits open lines into **confirmed** (header `PoHeader = 'confirmed'`) and **unconfirmed** (any other value, NULL, or missing header) buckets in one pass.
3. **Calculate** — `final = max(0, on_hand + confirmed_po - in_progress - committed)` for each barcode found in Shopify; discontinued barcodes force `final = 0`. Only supplier-confirmed PO quantity counts toward sellable stock — unconfirmed quantity is never added (the supplier has not committed to it), but it is recorded in `product_logs.unconfirmed_po_quantity` and shown in the Logs page so a stock drop is always explainable.
4. **Update Shopify** — Sets inventory quantities, publishes/unpublishes products based on stock, logs every action to PostgreSQL `product_logs`.

### Key Modules

- **`main.py`** — Flask app, all API routes, singleton `PostgresManager` instance
- **`database.py`** — `PostgresManager` (app state in PostgreSQL) + `MSSQLManager` (read-only queries against external SQL Server)
- **`sync_engine.py`** — `run_sync(store_id, pg)` orchestrates the 4-phase sync
- **`shopify_client.py`** — Shopify GraphQL client (Admin API `2026-04`, pinned in `API_VERSION`). Rate-limit tracking (sleeps when <100 points available) plus retry-with-backoff in `_request()` for all reads and writes: network/timeout, 429/5xx, GraphQL `THROTTLED`, and OAuth 401 re-auth. Inventory writes use `inventorySetQuantities` with a mandatory `@idempotent` key (so retries never double-apply) and opt out of compare-and-swap via `changeFromQuantity: null`.
- **`scheduler.py`** — Per-store background scheduler using daemon threads with `threading.Event` for interruptible sleep. Interval is in minutes (min 5, enforced in `main.py`); the wait between runs polls every 15s so `update_interval()` applies to a running scheduler without triggering an out-of-band sync

### Data Storage

PostgreSQL holds all app state (schema auto-created via `SCHEMA_SQL` in `database.py`):
- `stores` — Shopify store credentials and sync config (`sync_interval_minutes`, default 360)
- `store_locations` — Which Shopify location to sync (one active per store)
- `sql_config` — MSSQL connection details (keys: `s2s`, `db_admin`)
- `sync_runs` — Execution log per sync (status: running/success/partial/failed)
- `product_logs` — Per-product action log (skip/inventory_update/publish/unpublish/error)

### Stuck Sync Protection

- `has_running_sync()` auto-marks syncs older than 15 minutes as failed before checking
- `POST /api/sync/<id>/cancel` manually cancels stuck syncs
- Dashboard shows "Cancel Sync" button when a sync is in `running` state

### Frontend

Vanilla JS pages served by Flask templates. No build step. Each page has a corresponding JS file in `app/static/js/`. Material Design 3 styling in `app/static/css/style.css`.

## Environment

Configured via `docker-compose.yml`:
- `DATABASE_URL=postgresql://inventory:inventory@db:5432/inventory_sync`
- `FLASK_ENV=development`

MSSQL and Shopify credentials are stored in PostgreSQL tables, configured through the web UI.

## Deployment

Ubuntu 24 LTS: `install.sh` handles Docker installation, repo cloning to `/opt/inventory-sync`, and container management (install/update/status/remove).
