import time
import logging
import threading
import requests

logger = logging.getLogger(__name__)


class TokenManager:
    """Manages OAuth Client Credentials token exchange and caching for Shopify."""

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()

    def get_token(self, store_url, client_id, client_secret):
        cache_key = (store_url, client_id)

        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached["expires_at"] > time.time() + 300:
                return cached["token"]

        token_data = self._exchange_token(store_url, client_id, client_secret)

        with self._lock:
            self._cache[cache_key] = {
                "token": token_data["access_token"],
                "expires_at": time.time() + token_data["expires_in"],
            }

        return token_data["access_token"]

    def _exchange_token(self, store_url, client_id, client_secret):
        clean_url = store_url.rstrip("/")
        if not clean_url.startswith("https://"):
            clean_url = f"https://{clean_url}"
        url = f"{clean_url}/admin/oauth/access_token"

        logger.info("Exchanging OAuth credentials for store %s", store_url)
        resp = requests.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info(
            "Token acquired for store %s (expires in %ds)",
            store_url,
            data.get("expires_in", 0),
        )
        return data

    def invalidate(self, store_url, client_id):
        with self._lock:
            self._cache.pop((store_url, client_id), None)


token_manager = TokenManager()
