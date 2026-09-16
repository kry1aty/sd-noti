"""Async client for ELMA365 Web API."""
import logging
from typing import Dict, List, Optional, Any, Tuple
import httpx
from config import settings

logger = logging.getLogger("sd_notif.elma")

# Тайм-аут по умолчанию для всех запросов к ELMA
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


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

    async def get_active_sessions(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Fetch all active (non-closed) sessions from _lines/_sessions."""
        url = f"{self.base_url}/pub/v1/app/_lines/_sessions/list"
        payload = {
            "size": 100,
            "sort": [{"field": "__createdAt", "order": "desc"}]
        }
        try:
            resp = await client.post(url, headers=self.headers, json=payload, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 200:
                sessions = resp.json().get("result", {}).get("result", [])
                active = []
                for s in sessions:
                    st = s.get("_state")
                    code = st[0].get("code") if isinstance(st, list) and st else str(st)
                    if code != "closed":
                        active.append(s)
                return active
            else:
                logger.error(f"Failed to fetch sessions: HTTP {resp.status_code} - {resp.text}")
        except httpx.TimeoutException:
            logger.error("Timeout fetching active sessions from ELMA")
        except Exception as e:
            logger.exception(f"Exception fetching sessions: {e}")
        return []

    async def get_applications(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        """Fetch applications (tickets) from service_desk/applications."""
        url = f"{self.base_url}/pub/v1/app/service_desk/applications/list"
        payload = {
            "size": 100,
            "sort": [{"field": "__createdAt", "order": "desc"}]
        }
        try:
            resp = await client.post(url, headers=self.headers, json=payload, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("result", [])
            else:
                logger.error(f"Failed to fetch applications: HTTP {resp.status_code} - {resp.text}")
        except httpx.TimeoutException:
            logger.error("Timeout fetching applications from ELMA")
        except Exception as e:
            logger.exception(f"Exception fetching applications: {e}")
        return []

    async def get_application_by_id(self, client: httpx.AsyncClient, app_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a specific application by its ID (lazy loading)."""
        if app_id in self._app_cache:
            return self._app_cache[app_id]
        url = f"{self.base_url}/pub/v1/app/service_desk/applications/{app_id}/get"
        try:
            resp = await client.get(url, headers=self.headers, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 200:
                item = resp.json().get("item", {})
                if item:
                    self._app_cache[app_id] = item
                return item
            else:
                logger.error(f"Failed to get application {app_id}: HTTP {resp.status_code} - {resp.text}")
        except httpx.TimeoutException:
            logger.error(f"Timeout fetching application {app_id}")
        except Exception as e:
            logger.exception(f"Exception fetching application {app_id}: {e}")
        return None

    async def get_totals_and_active(self, client: httpx.AsyncClient) -> Tuple[int, int, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Fetch real total counts from ELMA365 along with items."""
        sessions, apps = [], []
        total_sessions, total_apps = 0, 0

        # Sessions
        sess_url = f"{self.base_url}/pub/v1/app/_lines/_sessions/list"
        try:
            r_sess = await client.post(
                sess_url,
                headers=self.headers,
                json={"size": 100, "sort": [{"field": "__createdAt", "order": "desc"}]},
                timeout=DEFAULT_TIMEOUT,
            )
            sess_res = r_sess.json().get("result", {}) if r_sess.status_code == 200 else {}
            total_sessions = sess_res.get("total", len(sess_res.get("result", [])))
            sessions = sess_res.get("result", [])
        except Exception as e:
            logger.error(f"Failed to fetch total sessions: {e}")
        
        active_sessions = []
        for s in sessions:
            st = s.get("_state")
            code = st[0].get("code") if isinstance(st, list) and st else str(st)
            if code != "closed":
                active_sessions.append(s)

        # Applications
        apps_url = f"{self.base_url}/pub/v1/app/service_desk/applications/list"
        try:
            r_apps = await client.post(
                apps_url,
                headers=self.headers,
                json={"size": 100, "sort": [{"field": "__createdAt", "order": "desc"}]},
                timeout=DEFAULT_TIMEOUT,
            )
            apps_res = r_apps.json().get("result", {}) if r_apps.status_code == 200 else {}
            total_apps = apps_res.get("total", len(apps_res.get("result", [])))
            apps = apps_res.get("result", [])
        except Exception as e:
            logger.error(f"Failed to fetch total applications: {e}")

        # Prepopulate cache
        for a in apps:
            aid = a.get("__id")
            if aid:
                self._app_cache[aid] = a

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