import time
import logging
from datetime import datetime, timezone

from app.database import PostgresManager, MSSQLManager
from app.shopify_client import ShopifyClient

logger = logging.getLogger(__name__)


def run_sync(store_id, pg):
    store = pg.get_store(store_id)
    if not store:
        raise ValueError(f"Store {store_id} not found")

    if pg.has_running_sync(store_id):
        raise ValueError(f"Sync already running for store {store_id}")

    s2s_config = pg.get_sql_config("s2s")
    if not s2s_config:
        raise ValueError("S2S SQL config not configured")

    s2s_time_conn = MSSQLManager(
        s2s_config["host"],
        s2s_config["port"],
        s2s_config["database_name"],
        s2s_config["username"],
        s2s_config["password"],
    )
    sql_server_start = s2s_time_conn.get_server_time()
    logger.info("SQL Server time at sync start: %s", sql_server_start)

    sync_run = pg.create_sync_run(store_id, started_at=sql_server_start)
    run_id = sync_run["id"]
    start_time = time.time()

    counters = {
        "total_products": 0,
        "products_updated": 0,
        "products_published": 0,
        "products_unpublished": 0,
        "products_skipped": 0,
        "errors_count": 0,
    }
    product_logs = []

    try:
        admin_config = pg.get_sql_config("db_admin")
        if not admin_config:
            raise ValueError("DB_ADMIN SQL config not configured")

        location = pg.get_active_location(store_id)
        if not location:
            raise ValueError("No active location configured for this store")
        location_id = location["location_id"]

        publication_id = store.get("publication_id")

        s2s = MSSQLManager(
            s2s_config["host"],
            s2s_config["port"],
            s2s_config["database_name"],
            s2s_config["username"],
            s2s_config["password"],
        )
        db_admin = MSSQLManager(
            admin_config["host"],
            admin_config["port"],
            admin_config["database_name"],
            admin_config["username"],
            admin_config["password"],
        )

        shopify = ShopifyClient(store["store_url"], store["admin_access_token"])

        logger.info("=== Phase 1: Fetching Shopify variants at location %s ===", location_id)
        phase_start = time.time()
        variants = shopify.get_all_variants(location_id)
        logger.info(
            "Found %d variants with barcodes at location (%.1fs)",
            len(variants),
            time.time() - phase_start,
        )

        logger.info("=== Phase 2: Fetching SQL Server data ===")
        phase_start = time.time()
        on_hand = s2s.get_on_hand_quantities()
        logger.info("S2S on-hand: %d UPCs (%.1fs)", len(on_hand), time.time() - phase_start)

        phase_start = time.time()
        pending_po = s2s.get_pending_po_quantities()
        logger.info("S2S pending PO: %d UPCs (%.1fs)", len(pending_po), time.time() - phase_start)

        phase_start = time.time()
        in_progress = db_admin.get_in_progress_quantities()
        logger.info("DB_ADMIN in-progress: %d UPCs (%.1fs)", len(in_progress), time.time() - phase_start)

        logger.info("=== Phase 3: Calculating inventory ===")
        shopify_barcodes = set(variants.keys())
        inventory = {}
        for upc in shopify_barcodes:
            oh = on_hand.get(upc, 0)
            po = pending_po.get(upc, 0)
            ip = in_progress.get(upc, 0)
            final = int(oh + po - ip)
            inventory[upc] = {
                "final": final,
                "on_hand": oh,
                "pending_po": po,
                "in_progress": ip,
            }
        logger.info("Matched %d Shopify barcodes against SQL data", len(inventory))

        counters["total_products"] = len(inventory)

        logger.info("=== Phase 4: Updating Shopify inventory ===")
        processed = 0
        for upc, inv_data in inventory.items():
            variant = variants[upc]

            new_qty = inv_data["final"]
            old_qty = variant["inventory_quantity"]
            log_entry = {
                "sync_run_id": run_id,
                "store_id": store_id,
                "product_upc": upc,
                "product_description": variant.get("product_title"),
                "shopify_variant_id": variant["variant_id"],
                "shopify_product_id": variant["product_id"],
                "old_quantity": old_qty,
                "new_quantity": new_qty,
                "quantity_on_hand": inv_data["on_hand"],
                "pending_po_quantity": inv_data["pending_po"],
                "in_progress_quantity": inv_data["in_progress"],
                "action": "skip",
                "error_message": None,
                "created_at": sql_server_start,
            }

            try:
                if new_qty != old_qty:
                    shopify.set_inventory_quantity(
                        variant["inventory_item_id"], location_id, new_qty
                    )
                    log_entry["action"] = "inventory_update"
                    counters["products_updated"] += 1

                if publication_id:
                    is_published = shopify.is_product_published(
                        variant["product_id"], publication_id
                    )

                    if new_qty <= 0 and is_published:
                        shopify.unpublish_product(variant["product_id"], publication_id)
                        log_entry["action"] = "unpublish"
                        counters["products_unpublished"] += 1
                    elif new_qty > 0 and not is_published:
                        shopify.publish_product(variant["product_id"], publication_id)
                        log_entry["action"] = "republish"
                        counters["products_published"] += 1

                if log_entry["action"] == "skip":
                    counters["products_skipped"] += 1

            except Exception as e:
                logger.error("Error processing UPC %s: %s", upc, str(e))
                log_entry["action"] = "error"
                log_entry["error_message"] = str(e)
                counters["errors_count"] += 1

            product_logs.append(log_entry)

            processed += 1
            if processed % 500 == 0:
                logger.info("Processing %d/%d products...", processed, len(inventory))

        if product_logs:
            pg.create_product_logs_batch(product_logs)

        duration = time.time() - start_time
        sql_server_end = s2s_time_conn.get_server_time()
        status = "success" if counters["errors_count"] == 0 else "partial"
        pg.update_sync_run(
            run_id,
            {
                "finished_at": sql_server_end,
                "status": status,
                "duration_seconds": round(duration, 2),
                **counters,
            },
        )
        pg.update_store(
            store_id,
            {"last_sync_at": sql_server_end},
        )

        logger.info(
            "Sync completed: %d updated, %d published, %d unpublished, %d skipped, %d errors (%.1fs)",
            counters["products_updated"],
            counters["products_published"],
            counters["products_unpublished"],
            counters["products_skipped"],
            counters["errors_count"],
            duration,
        )
        return {
            "run_id": run_id,
            "status": status,
            "counters": counters,
            "duration": round(duration, 2),
        }

    except Exception as e:
        duration = time.time() - start_time
        logger.error("Sync failed for store %s: %s", store_id, str(e))

        if product_logs:
            pg.create_product_logs_batch(product_logs)

        try:
            sql_server_end = s2s_time_conn.get_server_time()
        except Exception:
            sql_server_end = datetime.now(timezone.utc)

        pg.update_sync_run(
            run_id,
            {
                "finished_at": sql_server_end,
                "status": "failed",
                "error_message": str(e),
                "duration_seconds": round(duration, 2),
                **counters,
            },
        )
        return {
            "run_id": run_id,
            "status": "failed",
            "error": str(e),
            "counters": counters,
            "duration": round(duration, 2),
        }
