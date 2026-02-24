import time
import logging
import requests

logger = logging.getLogger(__name__)

API_VERSION = "2025-01"
RATE_LIMIT_THRESHOLD = 100
RATE_LIMIT_SLEEP = 1.0


class ShopifyClient:
    def __init__(self, store_url, admin_access_token):
        self.store_url = store_url.rstrip("/")
        if not self.store_url.startswith("https://"):
            self.store_url = f"https://{self.store_url}"
        self.token = admin_access_token
        self.graphql_url = f"{self.store_url}/admin/api/{API_VERSION}/graphql.json"
        self.available_points = 1000

    def _request(self, query, variables=None):
        headers = {
            "X-Shopify-Access-Token": self.token,
            "Content-Type": "application/json",
        }
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        if self.available_points < RATE_LIMIT_THRESHOLD:
            logger.info(
                "Rate limit low (%d points), sleeping %.1fs",
                self.available_points,
                RATE_LIMIT_SLEEP,
            )
            time.sleep(RATE_LIMIT_SLEEP)

        resp = requests.post(self.graphql_url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        extensions = result.get("extensions", {})
        cost = extensions.get("cost", {})
        throttle = cost.get("throttleStatus", {})
        if "currentlyAvailable" in throttle:
            self.available_points = throttle["currentlyAvailable"]

        if "errors" in result and result["errors"]:
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
                name
              }
            }
          }
        }
        """
        data = self._request(query)
        publications = []
        for edge in data.get("publications", {}).get("edges", []):
            node = edge["node"]
            publications.append({"id": node["id"], "name": node["name"]})
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
                  quantities(names: ["available"]) {
                    quantity
                  }
                  item {
                    id
                    variant {
                      id
                      barcode
                      product {
                        id
                        title
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
                  quantities(names: ["available"]) {
                    quantity
                  }
                  item {
                    id
                    variant {
                      id
                      barcode
                      product {
                        id
                        title
                        publishedOnPublication(publicationId: $publicationId)
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
                variant = item.get("variant")
                if not variant:
                    continue
                barcode = variant.get("barcode")
                if barcode:
                    quantities = node.get("quantities", [])
                    available = quantities[0]["quantity"] if quantities else 0
                    result_entry = {
                        "variant_id": variant["id"],
                        "inventory_quantity": available,
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
        mutation inventorySetQuantities($input: InventorySetQuantitiesInput!) {
          inventorySetQuantities(input: $input) {
            inventoryAdjustmentGroup {
              createdAt
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        variables = {
            "input": {
                "name": "available",
                "reason": "correction",
                "ignoreCompareQuantity": True,
                "quantities": [
                    {
                        "inventoryItemId": inventory_item_id,
                        "locationId": location_id,
                        "quantity": quantity,
                    }
                ],
            }
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
        mutation inventorySetQuantities($input: InventorySetQuantitiesInput!) {
          inventorySetQuantities(input: $input) {
            inventoryAdjustmentGroup {
              createdAt
            }
            userErrors {
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
            }
            for item in items
        ]
        variables = {
            "input": {
                "name": "available",
                "reason": "correction",
                "ignoreCompareQuantity": True,
                "quantities": quantities,
            }
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
