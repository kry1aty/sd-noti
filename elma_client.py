"""Async client for ELMA365 Web API."""
import logging
from typing import Dict, List, Optional, Any, Tuple
import httpx
from config import settings

logger = logging.getLogger("sd_notif.elma")

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_CACHE_SIZE = 2000


class ElmaClient:
    def __init__(self):
        raw_url = settings.ELMA_BASE_URL.rstrip("/")
        if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
            raw_url = f"https://{raw_url}"
        self.base_url = raw_url
        self.headers = {
            "Authorization": f"Bearer {settings.ELMA_API_TOKEN}",
            "Content-Type": "application/json",
        }
        self._app_cache: Dict[str, Dict[str, Any]] = {}

    def _put_cache(self, key: str, value: Dict[str, Any]):
        if len(self._app_cache) >= MAX_CACHE_SIZE:
            # Drop oldest 20% of entries to keep memory bounded
            keys_to_remove = list(self._app_cache.keys())[: MAX_CACHE_SIZE // 5]
            for k in keys_to_remove:
                self._app_cache.pop(k, None)
        self._app_cache[key] = value

    async def _fetch_paged(
        self, client: httpx.AsyncClient, path: str, max_items: int = 500
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """Fetch items with pagination up to max_items."""
        url = f"{self.base_url}{path}"
        page_size = min(settings.ELMA_PAGE_SIZE, 100)
        items: List[Dict[str, Any]] = []
        offset = 0
        total = 0

        while offset < max_items:
            payload = {
                "size": page_size,
                "from": offset,
                "sort": [{"field": "__createdAt", "order": "desc"}],
            }
            try:
                resp = await client.post(url, headers=self.headers, json=payload, timeout=DEFAULT_TIMEOUT)
                if resp.status_code != 200:
                    logger.error(f"Failed to fetch {path} (offset {offset}): HTTP {resp.status_code} - {resp.text}")
                    break

                res_data = resp.json().get("result", {})
                total = res_data.get("total", total)
                batch = res_data.get("result", [])
                if not batch:
                    break

                items.extend(batch)
                offset += len(batch)

                # Stop if we received all items available
                if offset >= total or len(batch) < page_size:
                    break
            except httpx.TimeoutException:
                logger.error(f"Timeout fetching {path} at offset {offset}")
                break
            except Exception as e:
                logger.exception(f"Exception fetching {path} at offset {offset}: {e}")
                break

        return total or len(items), items

    async def get_active_sessions(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Fetch all active (non-closed) sessions from _lines/_sessions."""
        _, sessions = await self._fetch_paged(
            client, "/pub/v1/app/_lines/_sessions/list", max_items=settings.ELMA_MAX_ITEMS_FETCH
        )
        active = []
        for s in sessions:
            st = s.get("_state")
            code = st[0].get("code") if isinstance(st, list) and st else str(st)
            if code != "closed":
                active.append(s)
        return active

    async def get_applications(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Fetch applications (tickets) from service_desk/applications."""
        _, apps = await self._fetch_paged(
            client, "/pub/v1/app/service_desk/applications/list", max_items=settings.ELMA_MAX_ITEMS_FETCH
        )
        for a in apps:
            aid = a.get("__id")
            if aid:
                self._put_cache(aid, a)
        return apps

    async def get_application_by_id(self, client: httpx.AsyncClient, app_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a specific application by its ID (lazy loading with bounded cache)."""
        if app_id in self._app_cache:
            return self._app_cache[app_id]
        url = f"{self.base_url}/pub/v1/app/service_desk/applications/{app_id}/get"
        try:
            resp = await client.get(url, headers=self.headers, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 200:
                item = resp.json().get("item", {})
                if item:
                    self._put_cache(app_id, item)
                return item
            else:
                logger.error(f"Failed to get application {app_id}: HTTP {resp.status_code} - {resp.text}")
        except httpx.TimeoutException:
            logger.error(f"Timeout fetching application {app_id}")
        except Exception as e:
            logger.exception(f"Exception fetching application {app_id}: {e}")
        return None

    async def get_totals_and_active(
        self, client: httpx.AsyncClient
    ) -> Tuple[int, int, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Fetch real total counts from ELMA365 along with items."""
        total_sessions, sessions = await self._fetch_paged(
            client, "/pub/v1/app/_lines/_sessions/list", max_items=settings.ELMA_MAX_ITEMS_FETCH
        )
        total_apps, apps = await self._fetch_paged(
            client, "/pub/v1/app/service_desk/applications/list", max_items=settings.ELMA_MAX_ITEMS_FETCH
        )

        active_sessions = []
        for s in sessions:
            st = s.get("_state")
            code = st[0].get("code") if isinstance(st, list) and st else str(st)
            if code != "closed":
                active_sessions.append(s)

        for a in apps:
            aid = a.get("__id")
            if aid:
                self._put_cache(aid, a)

        return total_sessions, total_apps, active_sessions, apps

    async def get_session_by_id(self, client: httpx.AsyncClient, session_id: str) -> Optional[Dict[str, Any]]:
        """Get specific session details."""
        url = f"{self.base_url}/pub/v1/app/_lines/_sessions/{session_id}/get"
        try:
            resp = await client.get(url, headers=self.headers, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 200:
                return resp.json().get("item", {})
        except httpx.TimeoutException:
            logger.error(f"Timeout fetching session {session_id}")
        except Exception as e:
            logger.exception(f"Exception fetching session {session_id}: {e}")
        return None

    async def close_session(self, client: httpx.AsyncClient, session_id: str) -> bool:
        """Close line session via ELMA365 API."""
        url = f"{self.base_url}/pub/v1/app/_lines/_sessions/{session_id}/update"
        payload = {
            "context": {
                "_state": [{"code": "closed", "name": "Закрыта"}]
            }
        }
        try:
            resp = await client.post(url, headers=self.headers, json=payload, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 200:
                logger.info(f"Session {session_id} successfully closed in ELMA365")
                return True
            else:
                logger.error(f"Failed to close session {session_id}: HTTP {resp.status_code} - {resp.text}")
        except httpx.TimeoutException:
            logger.error(f"Timeout closing session {session_id}")
        except Exception as e:
            logger.exception(f"Exception closing session {session_id}: {e}")
        return False