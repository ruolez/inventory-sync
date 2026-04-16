import time
import logging
from datetime import datetime, timezone

from app.database import PostgresManager, MSSQLManager
from app.shopify_client import ShopifyClient, create_shopify_client

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
        "products_skip_unpublish": 0,
        "products_discontinued": 0,
        "products_excluded": 0,
        "products_skipped": 0,
        "errors_count": 0,
    }
    product_logs = []
    excluded_upcs = pg.get_excluded_upcs_set()
    excluded_found = set()

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

        shopify = create_shopify_client(store)

        logger.info("=== Phase 1: Fetching Shopify variants at location %s ===", location_id)
        phase_start = time.time()
        variants = shopify.get_all_variants(location_id, publication_id=publication_id)
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

        phase_start = time.time()
        discontinued = s2s.get_discontinued_barcodes()
        logger.info("S2S discontinued: %d UPCs (%.1fs)", len(discontinued), time.time() - phase_start)

        logger.info("=== Phase 3: Calculating inventory ===")
        shopify_barcodes = set(variants.keys())

        excluded_found = shopify_barcodes & excluded_upcs
        if excluded_found:
            shopify_barcodes -= excluded_found
            counters["products_excluded"] = len(excluded_found)
            logger.info("Excluded %d products from sync", len(excluded_found))

        inventory = {}
        for upc in shopify_barcodes:
            oh = on_hand.get(upc, 0)
            po = pending_po.get(upc, 0)
            ip = in_progress.get(upc, 0)
            is_discontinued = upc in discontinued
            final = 0 if is_discontinued else max(0, int(oh + po - ip))
            inventory[upc] = {
                "final": final,
                "on_hand": oh,
                "pending_po": po,
                "in_progress": ip,
                "discontinued": is_discontinued,
            }
        logger.info("Matched %d Shopify barcodes against SQL data", len(inventory))

        counters["total_products"] = len(inventory)
        pg.update_sync_run(run_id, counters)

        logger.info("=== Phase 4A: Classifying products ===")
        log_entries_map = {}
        items_to_update = []
        products_to_publish = {}

        for upc, inv_data in inventory.items():
            variant = variants[upc]
            new_qty = inv_data["final"]
            old_qty = variant["inventory_quantity"]
            log_entry = {
                "sync_run_id": run_id,
                "store_id": store_id,
                "product_upc": upc[:50],
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
            log_entries_map[upc] = log_entry

            if inv_data["discontinued"]:
                log_entry["action"] = "discontinued"
                counters["products_discontinued"] += 1
                if new_qty != old_qty:
                    items_to_update.append({
                        "inventory_item_id": variant["inventory_item_id"],
                        "quantity": new_qty,
                        "upc": upc,
                    })
                    counters["products_updated"] += 1
            elif new_qty != old_qty:
                log_entry["action"] = "inventory_update"
                counters["products_updated"] += 1
                items_to_update.append({
                    "inventory_item_id": variant["inventory_item_id"],
                    "quantity": new_qty,
                    "upc": upc,
                })

                if publication_id:
                    product_id = variant["product_id"]
                    is_published = variant.get("is_published", False)
                    if new_qty > 0 and not is_published:
                        products_to_publish[product_id] = upc
            else:
                counters["products_skipped"] += 1

        logger.info(
            "Classification: %d to update, %d to publish, %d skipped",
            len(items_to_update),
            len(products_to_publish),
            counters["products_skipped"],
        )
        pg.update_sync_run(run_id, counters)

        zero_stock_items = {}
        for upc, inv_data in inventory.items():
            if inv_data["final"] <= 0 and not inv_data["discontinued"]:
                variant = variants[upc]
                zero_stock_items[variant["inventory_item_id"]] = upc

        if zero_stock_items:
            logger.info(
                "=== Phase 4B2: Checking other-location stock for %d zero-stock items ===",
                len(zero_stock_items),
            )
            other_stock = shopify.get_inventory_levels_for_items(
                list(zero_stock_items.keys()), location_id
            )

            items_to_update_by_iid = {item["inventory_item_id"]: item for item in items_to_update}
            override_count = 0

            for item_id, upc in zero_stock_items.items():
                max_qty = other_stock.get(item_id, 0)
                if max_qty <= 0:
                    continue

                variant = variants[upc]
                log_entry = log_entries_map[upc]
                log_entry["new_quantity"] = max_qty
                log_entry["action"] = "inventory_override"
                override_count += 1
                counters["products_skip_unpublish"] += 1

                if item_id in items_to_update_by_iid:
                    items_to_update_by_iid[item_id]["quantity"] = max_qty
                else:
                    items_to_update.append({
                        "inventory_item_id": item_id,
                        "quantity": max_qty,
                        "upc": upc,
                    })
                    counters["products_skipped"] -= 1
                    counters["products_updated"] += 1

                if publication_id:
                    product_id = variant["product_id"]
                    if not variant.get("is_published", False) and product_id not in products_to_publish:
                        products_to_publish[product_id] = upc

                logger.info(
                    "Override: UPC %s inventory_item %s set to %d (from other location)",
                    upc, item_id, max_qty,
                )

            logger.info(
                "Phase 4B2 result: %d overrides out of %d zero-stock items",
                override_count, len(zero_stock_items),
            )
            pg.update_sync_run(run_id, counters)

        logger.info("=== Phase 4B: Batch inventory updates (%d items) ===", len(items_to_update))
        batch_size = 250
        for i in range(0, len(items_to_update), batch_size):
            batch = items_to_update[i : i + batch_size]
            try:
                shopify.set_inventory_quantities_batch(batch, location_id)
            except Exception as batch_err:
                logger.warning(
                    "Batch %d-%d failed (%s), falling back to individual updates",
                    i, i + len(batch), str(batch_err),
                )
                for item in batch:
                    try:
                        shopify.set_inventory_quantity(
                            item["inventory_item_id"], location_id, item["quantity"]
                        )
                    except Exception as e:
                        upc = item["upc"]
                        logger.error("Error updating UPC %s: %s", upc, str(e))
                        log_entries_map[upc]["action"] = "error"
                        log_entries_map[upc]["error_message"] = str(e)
                        counters["products_updated"] -= 1
                        counters["errors_count"] += 1

            pg.update_sync_run(run_id, counters)
            if i + batch_size < len(items_to_update):
                logger.info(
                    "Inventory updates: %d/%d batched...",
                    min(i + batch_size, len(items_to_update)),
                    len(items_to_update),
                )

        if publication_id:
            logger.info(
                "=== Phase 4C: Publish (%d products) ===",
                len(products_to_publish),
            )
            for product_id, upc in products_to_publish.items():
                try:
                    shopify.publish_product(product_id, publication_id)
                    log_entries_map[upc]["action"] = "republish"
                    counters["products_published"] += 1
                except Exception as e:
                    logger.error("Error publishing product %s (UPC %s): %s", product_id, upc, str(e))
                    log_entries_map[upc]["action"] = "error"
                    log_entries_map[upc]["error_message"] = str(e)
                    counters["errors_count"] += 1

            pg.update_sync_run(run_id, counters)

        logger.info("=== Phase 4D: Finalizing ===")
        excluded_log_entries = []
        for upc in excluded_found:
            variant = variants[upc]
            excluded_log_entries.append({
                "sync_run_id": run_id,
                "store_id": store_id,
                "product_upc": upc[:50],
                "product_description": variant.get("product_title"),
                "shopify_variant_id": variant["variant_id"],
                "shopify_product_id": variant["product_id"],
                "old_quantity": variant["inventory_quantity"],
                "new_quantity": variant["inventory_quantity"],
                "quantity_on_hand": None,
                "pending_po_quantity": None,
                "in_progress_quantity": None,
                "action": "excluded",
                "error_message": None,
                "created_at": sql_server_start,
            })
        product_logs = list(log_entries_map.values()) + excluded_log_entries

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
            "Sync completed: %d updated, %d published, %d discontinued, %d excluded, %d overrides, %d skipped, %d errors (%.1fs)",
            counters["products_updated"],
            counters["products_published"],
            counters["products_discontinued"],
            counters["products_excluded"],
            counters["products_skip_unpublish"],
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

        try:
            if not product_logs and log_entries_map:
                product_logs = list(log_entries_map.values())
        except NameError:
            pass
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
