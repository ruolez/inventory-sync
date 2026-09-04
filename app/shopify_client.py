import time
import logging
import random
import uuid
import requests

logger = logging.getLogger(__name__)

API_VERSION = "2026-04"
RATE_LIMIT_THRESHOLD = 100
RATE_LIMIT_SLEEP = 1.0
MAX_RETRIES = 5
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 30.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ShopifyClient:
    def __init__(self, store_url, admin_access_token=None,
                 oauth_client_id=None, oauth_client_secret=None):
        self.store_url = store_url.rstrip("/")
        if not self.store_url.startswith("https://"):
            self.store_url = f"https://{self.store_url}"
        self._static_token = admin_access_token
        self._oauth_client_id = oauth_client_id
        self._oauth_client_secret = oauth_client_secret
        self._auth_method = "oauth" if oauth_client_id else "legacy"
        self.graphql_url = f"{self.store_url}/admin/api/{API_VERSION}/graphql.json"
        self.available_points = 1000

    @property
    def token(self):
        if self._auth_method == "legacy":
            return self._static_token
        from app.token_manager import token_manager
        return token_manager.get_token(
            self.store_url, self._oauth_client_id, self._oauth_client_secret
        )

    def _sleep_backoff(self, attempt, reason, override=None):
        if override is not None:
            delay = max(0.0, override)
        else:
            delay = min(RETRY_MAX_DELAY, RETRY_BASE_DELAY * (2 ** attempt))
            delay += random.uniform(0, delay * 0.25)
        logger.warning(
            "Shopify request retry %d/%d in %.1fs (%s)",
            attempt + 1, MAX_RETRIES, delay, reason,
        )
        time.sleep(delay)

    @staticmethod
    def _retry_after_seconds(resp):
        raw = resp.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_retryable_graphql(result):
        # Top-level THROTTLED errors and idempotency-concurrent userErrors are
        # transient and safe to retry; all other GraphQL errors are deterministic.
        for e in result.get("errors") or []:
            if (e.get("extensions") or {}).get("code") == "THROTTLED":
                return True
        for value in (result.get("data") or {}).values():
            if isinstance(value, dict):
                for ue in value.get("userErrors") or []:
                    if ue.get("code") == "IDEMPOTENCY_CONCURRENT_REQUEST":
                        return True
        return False

    def _request(self, query, variables=None):
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        reauthed = False
        attempt = 0
        while True:
            if self.available_points < RATE_LIMIT_THRESHOLD:
                logger.info(
                    "Rate limit low (%d points), sleeping %.1fs",
                    self.available_points,
                    RATE_LIMIT_SLEEP,
                )
                time.sleep(RATE_LIMIT_SLEEP)

            headers = {
                "X-Shopify-Access-Token": self.token,
                "Content-Type": "application/json",
            }

            try:
                resp = requests.post(
                    self.graphql_url, json=payload, headers=headers, timeout=30
                )
            except requests.exceptions.RequestException as e:
                if attempt < MAX_RETRIES:
                    self._sleep_backoff(attempt, f"network error: {e}")
                    attempt += 1
                    continue
                raise

            # OAuth token expiry: one-shot reauth, does not consume retry budget.
            if resp.status_code == 401 and self._auth_method == "oauth" and not reauthed:
                from app.token_manager import token_manager
                logger.warning("Got 401, invalidating cached token and retrying")
                token_manager.invalidate(self.store_url, self._oauth_client_id)
                reauthed = True
                continue

            if resp.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                self._sleep_backoff(
                    attempt,
                    f"HTTP {resp.status_code}",
                    override=self._retry_after_seconds(resp),
                )
                attempt += 1
                continue

            resp.raise_for_status()
            result = resp.json()

            extensions = result.get("extensions", {})
            cost = extensions.get("cost", {})
            throttle = cost.get("throttleStatus", {})
            if "currentlyAvailable" in throttle:
                self.available_points = throttle["currentlyAvailable"]

            if result.get("errors"):
                if self._is_retryable_graphql(result) and attempt < MAX_RETRIES:
                    self._sleep_backoff(attempt, "GraphQL throttled/transient")
                    attempt += 1
                    continue
                error_msgs = [e.get("message", str(e)) for e in result["errors"]]
                raise Exception(f"Shopify GraphQL errors: {'; '.join(error_msgs)}")

            return result.get("data", {})

    def test_connection(self):
        data = self._request("{ shop { name myshopifyDomain } }")
        shop = data.get("shop", {})
        return {
            "name": shop.get("name"),
            "domain": shop.get("myshopifyDomain"),
        }

    def get_locations(self):
        query = """
        {
          locations(first: 50) {
            edges {
              node {
                id
                name
                isActive
              }
            }
          }
        }
        """
        data = self._request(query)
        locations = []
        for edge in data.get("locations", {}).get("edges", []):
            node = edge["node"]
            locations.append(
                {
                    "id": node["id"],
                    "name": node["name"],
                    "is_active": node["isActive"],
                }
            )
        return locations

    def get_publications(self):
        query = """
        {
          publications(first: 20) {
            edges {
              node {
                id
                catalog {
                  title
                }
              }
            }
          }
        }
        """
        data = self._request(query)
        publications = []
        for edge in data.get("publications", {}).get("edges", []):
            node = edge["node"]
            name = (node.get("catalog") or {}).get("title") or ""
            publications.append({"id": node["id"], "name": name})
        return publications

    def get_online_store_publication(self):
        pubs = self.get_publications()
        for pub in pubs:
            if "online store" in pub["name"].lower():
                return pub
        return pubs[0] if pubs else None

    def get_all_variants(self, location_id, publication_id=None):
        base_query = """
        query($locationId: ID!, $cursor: String) {
          location(id: $locationId) {
            inventoryLevels(first: 250, after: $cursor) {
              edges {
                node {
                  quantities(names: ["available", "committed"]) {
                    name
                    quantity
                  }
                  item {
                    id
                    variants(first: 1) {
                      edges {
                        node {
                          id
                          barcode
                          sku
                          product {
                            id
                            title
                            status
                          }
                        }
                      }
                    }
                  }
                }
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
        """
        pub_query = """
        query($locationId: ID!, $cursor: String, $publicationId: ID!) {
          location(id: $locationId) {
            inventoryLevels(first: 250, after: $cursor) {
              edges {
                node {
                  quantities(names: ["available", "committed"]) {
                    name
                    quantity
                  }
                  item {
                    id
                    variants(first: 1) {
                      edges {
                        node {
                          id
                          barcode
                          sku
                          product {
                            id
                            title
                            status
                            publishedOnPublication(publicationId: $publicationId)
                          }
                        }
                      }
                    }
                  }
                }
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
        """
        query = pub_query if publication_id else base_query
        results = {}
        cursor = None
        total_fetched = 0

        while True:
            variables = {"locationId": location_id}
            if publication_id:
                variables["publicationId"] = publication_id
            if cursor:
                variables["cursor"] = cursor
            data = self._request(query, variables)
            location_data = data.get("location", {})
            levels_data = location_data.get("inventoryLevels", {})
            edges = levels_data.get("edges", [])
            total_fetched += len(edges)

            for edge in edges:
                node = edge["node"]
                item = node.get("item", {})
                variant_edges = (item.get("variants") or {}).get("edges", [])
                variant = variant_edges[0]["node"] if variant_edges else None
                if not variant:
                    continue
                barcode = variant.get("barcode")
                if barcode:
                    product_status = variant["product"].get("status")
                    if product_status != "ACTIVE":
                        continue
                    qty_by_name = {
                        q["name"]: q["quantity"] for q in node.get("quantities", []) or []
                    }
                    available = qty_by_name.get("available", 0)
                    committed = qty_by_name.get("committed", 0)
                    result_entry = {
                        "variant_id": variant["id"],
                        "sku": variant.get("sku"),
                        "inventory_quantity": available,
                        "committed_quantity": committed,
                        "inventory_item_id": item["id"],
                        "product_id": variant["product"]["id"],
                        "product_title": variant["product"]["title"],
                    }
                    if publication_id:
                        result_entry["is_published"] = variant["product"].get(
                            "publishedOnPublication"
                        )
                    results[barcode] = result_entry

            logger.info(
                "Fetched %d inventory levels so far (%d with barcodes)...",
                total_fetched,
                len(results),
            )

            page_info = levels_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        return results

    def get_committed_by_barcode(self, location_id):
        query = """
        query($locationId: ID!, $cursor: String) {
          location(id: $locationId) {
            inventoryLevels(first: 250, after: $cursor) {
              edges {
                node {
                  quantities(names: ["committed"]) {
                    name
                    quantity
                  }
                  item {
                    variants(first: 1) {
                      edges {
                        node {
                          barcode
                        }
                      }
                    }
                  }
                }
              }
              pageInfo {
                hasNextPage
                endCursor
              }
            }
          }
        }
        """
        results = {}
        cursor = None
        total_fetched = 0

        while True:
            variables = {"locationId": location_id}
            if cursor:
                variables["cursor"] = cursor
            data = self._request(query, variables)
            location_data = data.get("location") or {}
            levels_data = location_data.get("inventoryLevels", {})
            edges = levels_data.get("edges", [])
            total_fetched += len(edges)

            for edge in edges:
                node = edge["node"]
                item = node.get("item") or {}
                variant_edges = (item.get("variants") or {}).get("edges", [])
                variant = variant_edges[0]["node"] if variant_edges else None
                if not variant:
                    continue
                barcode = variant.get("barcode")
                if not barcode:
                    continue
                qty_by_name = {
                    q["name"]: q["quantity"] for q in node.get("quantities", []) or []
                }
                committed = qty_by_name.get("committed", 0) or 0
                if committed:
                    results[barcode] = results.get(barcode, 0) + committed

            page_info = levels_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        logger.info(
            "Committed-by-barcode at location %s: %d barcodes with committed > 0 (scanned %d levels)",
            location_id,
            len(results),
            total_fetched,
        )
        return results

    def get_archived_committed_by_barcode(self):
        # Shopify keeps line items "committed" even after an order is archived
        # (closed) without being fulfilled. This sums those still-committed units
        # per barcode so the caller can exclude them from the committed total.
        # status:closed = archived (cancelled is a separate status and already
        # releases committed inventory); fulfillment_status:unfulfilled = null or partial.
        query = """
        query($cursor: String) {
          orders(first: 100, after: $cursor, query: "status:closed fulfillment_status:unfulfilled") {
            edges {
              node {
                id
                lineItems(first: 100) {
                  edges {
                    node {
                      unfulfilledQuantity
                      variant {
                        barcode
                      }
                    }
                  }
                }
              }
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
        }
        """
        results = {}
        cursor = None
        total_orders = 0

        while True:
            variables = {}
            if cursor:
                variables["cursor"] = cursor
            data = self._request(query, variables)
            orders_data = data.get("orders") or {}
            edges = orders_data.get("edges", [])
            total_orders += len(edges)

            for edge in edges:
                node = edge["node"]
                # Line items beyond the first 100 per order are not paginated; orders
                # with >100 distinct variants are not expected for this use case.
                for li_edge in (node.get("lineItems") or {}).get("edges", []):
                    li = li_edge["node"]
                    variant = li.get("variant")
                    if not variant:
                        continue
                    barcode = variant.get("barcode")
                    if not barcode:
                        continue
                    qty = li.get("unfulfilledQuantity", 0) or 0
                    if qty:
                        results[barcode] = results.get(barcode, 0) + qty

            page_info = orders_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        logger.info(
            "Archived-committed-by-barcode: %d barcodes still committed to archived orders (scanned %d orders)",
            len(results),
            total_orders,
        )
        return results

    def get_variants_by_barcodes(self, barcodes):
        if not barcodes:
            return {}

        results = {}
        batch_size = 50
        for i in range(0, len(barcodes), batch_size):
            batch = barcodes[i : i + batch_size]
            query_str = " OR ".join(f"barcode:{b}" for b in batch)
            query = """
            query($query: String!) {
              productVariants(first: 100, query: $query) {
                edges {
                  node {
                    id
                    barcode
                    inventoryQuantity
                    inventoryItem {
                      id
                    }
                    product {
                      id
                      title
                    }
                  }
                }
              }
            }
            """
            data = self._request(query, {"query": query_str})
            for edge in data.get("productVariants", {}).get("edges", []):
                node = edge["node"]
                barcode = node.get("barcode")
                if barcode:
                    results[barcode] = {
                        "variant_id": node["id"],
                        "inventory_quantity": node.get("inventoryQuantity", 0),
                        "inventory_item_id": node["inventoryItem"]["id"],
                        "product_id": node["product"]["id"],
                        "product_title": node["product"]["title"],
                    }
        return results

    def set_inventory_quantity(self, inventory_item_id, location_id, quantity):
        mutation = """
        mutation inventorySetQuantities($input: InventorySetQuantitiesInput!, $idempotencyKey: String!) {
          inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
            inventoryAdjustmentGroup {
              createdAt
            }
            userErrors {
              code
              field
              message
            }
          }
        }
        """
        variables = {
            "idempotencyKey": str(uuid.uuid4()),
            "input": {
                "name": "available",
                "reason": "correction",
                "quantities": [
                    {
                        "inventoryItemId": inventory_item_id,
                        "locationId": location_id,
                        "quantity": quantity,
                        "changeFromQuantity": None,
                    }
                ],
            },
        }
        data = self._request(mutation, variables)
        errors = (
            data.get("inventorySetQuantities", {}).get("userErrors", [])
        )
        if errors:
            error_msgs = [e.get("message", str(e)) for e in errors]
            raise Exception(f"Inventory update errors: {'; '.join(error_msgs)}")
        return True

    def set_inventory_quantities_batch(self, items, location_id):
        mutation = """
        mutation inventorySetQuantities($input: InventorySetQuantitiesInput!, $idempotencyKey: String!) {
          inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
            inventoryAdjustmentGroup {
              createdAt
            }
            userErrors {
              code
              field
              message
            }
          }
        }
        """
        quantities = [
            {
                "inventoryItemId": item["inventory_item_id"],
                "locationId": location_id,
                "quantity": item["quantity"],
                "changeFromQuantity": None,
            }
            for item in items
        ]
        variables = {
            "idempotencyKey": str(uuid.uuid4()),
            "input": {
                "name": "available",
                "reason": "correction",
                "quantities": quantities,
            },
        }
        data = self._request(mutation, variables)
        errors = (
            data.get("inventorySetQuantities", {}).get("userErrors", [])
        )
        if errors:
            error_msgs = [e.get("message", str(e)) for e in errors]
            raise Exception(f"Batch inventory update errors: {'; '.join(error_msgs)}")
        return True

    def is_product_published(self, product_id, publication_id):
        query = """
        query($productId: ID!, $publicationId: ID!) {
          product(id: $productId) {
            publishedOnPublication(publicationId: $publicationId)
          }
        }
        """
        data = self._request(
            query, {"productId": product_id, "publicationId": publication_id}
        )
        product = data.get("product", {})
        return product.get("publishedOnPublication", False)

    def publish_product(self, product_id, publication_id):
        mutation = """
        mutation publishablePublish($id: ID!, $input: [PublicationInput!]!) {
          publishablePublish(id: $id, input: $input) {
            publishable {
              availablePublicationsCount {
                count
              }
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        variables = {
            "id": product_id,
            "input": [{"publicationId": publication_id}],
        }
        data = self._request(mutation, variables)
        errors = data.get("publishablePublish", {}).get("userErrors", [])
        if errors:
            error_msgs = [e.get("message", str(e)) for e in errors]
            raise Exception(f"Publish errors: {'; '.join(error_msgs)}")
        return True

    def unpublish_product(self, product_id, publication_id):
        mutation = """
        mutation publishableUnpublish($id: ID!, $input: [PublicationInput!]!) {
          publishableUnpublish(id: $id, input: $input) {
            publishable {
              availablePublicationsCount {
                count
              }
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        variables = {
            "id": product_id,
            "input": [{"publicationId": publication_id}],
        }
        data = self._request(mutation, variables)
        errors = data.get("publishableUnpublish", {}).get("userErrors", [])
        if errors:
            error_msgs = [e.get("message", str(e)) for e in errors]
            raise Exception(f"Unpublish errors: {'; '.join(error_msgs)}")
        return True

    def get_inventory_levels_for_items(self, inventory_item_ids, exclude_location_id):
        query = """
        query($ids: [ID!]!) {
          nodes(ids: $ids) {
            ... on InventoryItem {
              id
              inventoryLevels(first: 50) {
                edges {
                  node {
                    location { id }
                    quantities(names: ["available"]) {
                      quantity
                    }
                  }
                }
              }
            }
          }
        }
        """
        result = {}
        batch_size = 100
        for i in range(0, len(inventory_item_ids), batch_size):
            batch = inventory_item_ids[i : i + batch_size]
            data = self._request(query, {"ids": batch})
            for node in data.get("nodes", []):
                if not node:
                    continue
                item_id = node.get("id")
                if not item_id:
                    continue
                max_other = 0
                for edge in node.get("inventoryLevels", {}).get("edges", []):
                    level = edge["node"]
                    loc_id = level.get("location", {}).get("id")
                    if loc_id == exclude_location_id:
                        continue
                    quantities = level.get("quantities", [])
                    qty = quantities[0]["quantity"] if quantities else 0
                    if qty > max_other:
                        max_other = qty
                result[item_id] = max_other
        return result


def create_shopify_client(store):
    """Create a ShopifyClient from a store dict (as returned by PostgresManager)."""
    if store.get("auth_method") == "oauth_client_credentials":
        return ShopifyClient(
            store["store_url"],
            oauth_client_id=store["oauth_client_id"],
            oauth_client_secret=store["oauth_client_secret"],
        )
    return ShopifyClient(store["store_url"], store["admin_access_token"])
