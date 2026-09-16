"""Watchdog engine for discrepancy alerting and shift handover scheduling."""
import asyncio
from datetime import datetime, timezone, timedelta
import html
import logging
import re
from typing import Dict, Any, List, Optional, Tuple
import httpx
from config import settings
from elma_client import ElmaClient
from bot_handler import BotHandler

logger = logging.getLogger("sd_notif.watchdog")


def normalize_uuid(raw_id: Optional[str]) -> str:
    """Extract clean UUID from raw strings that may contain web links or UI suffixes."""
    if not raw_id:
        return ""
    m = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', str(raw_id).lower())
    return m.group(0) if m else str(raw_id).strip()


def parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        dt_str = dt_str.replace("Z", "+00:00")
        if "." in dt_str:
            parts = dt_str.split(".")
            sec = parts[0]
            rest = parts[1]
            tz_part = ""
            if "+" in rest:
                rest, tz_part = rest.split("+")
                tz_part = "+" + tz_part
            rest = rest[:6]
            dt_str = f"{sec}.{rest}{tz_part}"
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def get_ticket_state(ticket: dict) -> str:
    """
    Returns canonical state: 'in_work', 'in_waiting', 'closed', 'cancelled', 'new'
    Uses native ELMA365 __status (4: in_work, 5: in_waiting, 6: closed/decided, 9: cancelled)
    """
    if ticket.get("__deletedAt") is not None:
        return "closed"

    # 1. Check native ELMA365 status ID
    s_obj = ticket.get("__status")
    st_num = s_obj.get("status") if isinstance(s_obj, dict) else s_obj
    if st_num == 4:
        return "in_work"
    elif st_num == 5:
        return "in_waiting"
    elif st_num == 6:
        return "closed"
    elif st_num == 9:
        return "cancelled"

    # 2. Check application_completed
    if ticket.get("application_completed") is True:
        return "closed"

    # 3. Fallback to changing_statuses history
    ch_st = ticket.get("changing_statuses", {})
    if isinstance(ch_st, dict):
        rows = ch_st.get("rows", [])
        if rows:
            code = str(rows[-1].get("status_code", "")).lower().strip()
            name = str(rows[-1].get("status_name", "")).lower().strip()
            if code in ["decided", "done", "closed", "completed"] or any(w in name for w in ["выполнен", "закрыт", "решен"]):
                return "closed"
            if code in ["cancelled", "rejected"] or any(w in name for w in ["отменен", "отклон"]):
                return "cancelled"
            if code == "in_work" or "в работе" in name:
                return "in_work"
            if code == "in_waiting" or "в ожидании" in name or "ждет" in name:
                return "in_waiting"

    return "new"


class WatchdogService:
    def __init__(self, elma: ElmaClient, bot: BotHandler):
        self.elma = elma
        self.bot = bot
        self.last_alerts: Dict[str, datetime] = {}
        self.last_shift_reported: Dict[str, str] = {}

    def is_ticket_closed(self, ticket: dict) -> bool:
        """Check if ticket status is strictly completed / closed."""
        state = get_ticket_state(ticket)
        return state in ["closed", "cancelled"]

    async def check_discrepancies(self, elma_client: httpx.AsyncClient, tg_client: Optional[httpx.AsyncClient] = None):
        """Scan ELMA365 strictly for assigned_to_operator + closed tickets beyond grace period."""
        tg_http = tg_client or elma_client
        now = datetime.now(timezone.utc)
        sessions = await self.elma.get_active_sessions(elma_client)
        apps = await self.elma.get_applications(elma_client)

        apps_by_session = {}
        for a in apps:
            norm_sid = normalize_uuid(a.get("session_id"))
            if norm_sid:
                apps_by_session[norm_sid] = a

        for s in sessions:
            sid = normalize_uuid(s.get("__id"))
            if not sid:
                continue

            # Strict filter: Session status MUST BE 'assigned_to_operator'
            st_list = s.get("_state", [])
            st_code = st_list[0].get("code") if isinstance(st_list, list) and st_list else str(s.get("_state"))
            if st_code != "assigned_to_operator":
                continue

            ticket = apps_by_session.get(sid)
            if not ticket:
                s_apps = s.get("_apps") or []
                if s_apps:
                    t_id = normalize_uuid(s_apps[-1].get("id"))
                    if t_id and len(t_id) > 8 and t_id != "none":
                        for a in apps:
                            if normalize_uuid(a.get("__id")) == t_id:
                                ticket = a
                                break
                        if not ticket:
                            ticket = await self.elma.get_application_by_id(elma_client, t_id)

            if not ticket or not self.is_ticket_closed(ticket):
                continue

            closed_at_str = ticket.get("__statusChangedAt") or ticket.get("__updatedAt")
            closed_at = parse_iso(closed_at_str) or now
            time_since_closed_mins = int((now - closed_at).total_seconds() / 60)

            if time_since_closed_mins < settings.GRACE_PERIOD_MINUTES:
                continue

            last_alert = self.last_alerts.get(sid)
            if last_alert and (now - last_alert).total_seconds() < settings.ALERT_COOLDOWN_MINUTES * 60:
                continue

            ticket_name = ticket.get("__name", "Заявка")
            ticket_link = ticket.get("link_to_application", "")
            sess_name = s.get("__name", "Сессия")
            st_name = st_list[0].get("name", "Назначена на оператора") if isinstance(st_list, list) and st_list else "Назначена на оператора"

            alert_text = (
                f"⚠️ <b>Внимание: Забытая сессия у закрытой заявки!</b>\n\n"
                f"📋 <b>Заявка:</b> <code>{html.escape(ticket_name)}</code> (<i>Выполнена/Закрыта</i>)\n"
                f"⏱ <b>Заявка закрыта:</b> {time_since_closed_mins} мин. назад\n"
                f"💬 <b>Сессия Линии:</b> <code>{sid}</code>\n"
                f"🏷 <b>Название:</b> {html.escape(sess_name)}\n"
                f"📊 <b>Статус сессии:</b> <i>{html.escape(st_name)}</i> (<code>assigned_to_operator</code>)"
            )

            close_deep_link = f"https://t.me/{settings.BOT_USERNAME}?start=close_{sid}"
            buttons = [
                [{"text": "🔒 Закрыть сессию (в ЛС бота)", "url": close_deep_link}]
            ]
            if ticket_link:
                buttons[0].append({"text": "🔗 Открыть в ELMA", "url": ticket_link})

            keyboard = {"inline_keyboard": buttons}

            await self.bot.send_message(
                tg_http,
                settings.TELEGRAM_GROUP_CHAT_ID,
                alert_text,
                reply_markup=keyboard,
                message_thread_id=settings.TELEGRAM_TOPIC_ID
            )
            self.last_alerts[sid] = now
            logger.info(f"Sent strict discrepancy alert for session {sid} to chat {settings.TELEGRAM_GROUP_CHAT_ID} thread {settings.TELEGRAM_TOPIC_ID}")

    def build_shift_handover_text(
        self,
        current_hm: str,
        total_sessions: int,
        total_apps: int,
        sessions: list,
        apps: list,
        apps_by_session: dict
    ) -> str:
        """Build formatted text for shift handover report."""
        in_work_count = 0
        in_waiting_count = 0

        for a in apps:
            st = get_ticket_state(a)
            if st == "in_work":
                in_work_count += 1
            elif st == "in_waiting":
                in_waiting_count += 1

        lines = [
            f"🔄 <b>Пересменка дежурной службы ServiceDesk ({current_hm} UTC+{settings.TIMEZONE_OFFSET_HOURS})</b>\n",
            "📢 <b>Внимание операторам!</b> Происходит передача дежурства смене.\n",
            f"📊 <b>Срез очереди на момент передачи смены:</b>",
            f"• Активных сессий в Линиях: <b>{len(sessions)}</b>",
            f"• Заявок в работе: <b>{in_work_count}</b>",
            f"• Заявок в ожидании: <b>{in_waiting_count}</b>",
            f"• <i>(Всего заявок в системе: {total_apps})</i>\n",
            "📋 <b>Список активных сессий:</b>"
        ]

        if not sessions:
            lines.append("<i>Все сессии закрыты, активных диалогов нет! 👍</i>")
        else:
            for idx, s in enumerate(sessions[:10], 1):
                sid = normalize_uuid(s.get("__id", ""))
                name = s.get("__name", "Сессия")
                st_list = s.get("_state", [])
                st_name = st_list[0].get("name") if isinstance(st_list, list) and st_list else "В очереди"
                
                linked = apps_by_session.get(sid)
                t_info = linked.get("__name", "Заявка") if linked else "без заявки"
                lines.append(f"{idx}. <code>{sid[:8]}...</code> ({html.escape(name)}) — <i>{html.escape(st_name)}</i> [Заявка: {html.escape(t_info)}]")
            
            if len(sessions) > 10:
                lines.append(f"<i>...и еще {len(sessions) - 10} сессий.</i>")

        lines.append("\n⚠️ <b>Памятка операторам:</b>")
        lines.append("1️⃣ <b>Сдающей смене:</b> завершите решенные сессии, передайте открытые заявки и переведите статус в ELMA в «Не в сети».\n2️⃣ <b>Заступающей смене:</b> переведите статус в «Онлайн / В сети» и примите нераспределенные диалоги.")

        return "\n".join(lines)

    async def check_shift_handover(self, elma_client: httpx.AsyncClient, tg_client: Optional[httpx.AsyncClient] = None):
        """Check if current local time (UTC+5) is 08:00 or 20:00 and send shift handover report."""
        tg_http = tg_client or elma_client
        tz = timezone(timedelta(hours=settings.TIMEZONE_OFFSET_HOURS))
        now_local = datetime.now(tz)
        current_hm = now_local.strftime("%H:%M")
        today_str = now_local.strftime("%Y-%m-%d")

        if current_hm in settings.SHIFT_TIMES:
            shift_key = f"{today_str}_{current_hm}"
            if self.last_shift_reported.get(current_hm) == today_str:
                return

            logger.info(f"Generating Shift Handover Report for {current_hm} UTC+{settings.TIMEZONE_OFFSET_HOURS}")
            total_sessions, total_apps, sessions, apps = await self.elma.get_totals_and_active(elma_client)

            apps_by_session = {}
            for a in apps:
                norm_sid = normalize_uuid(a.get("session_id"))
                if norm_sid:
                    apps_by_session[norm_sid] = a

            for s in sessions:
                sid = normalize_uuid(s.get("__id"))
                if sid and sid not in apps_by_session:
                    s_apps = s.get("_apps") or []
                    if s_apps:
                        t_id = normalize_uuid(s_apps[-1].get("id"))
                        if t_id and len(t_id) > 8 and t_id != "none":
                            t_obj = await self.elma.get_application_by_id(elma_client, t_id)
                            if t_obj:
                                apps_by_session[sid] = t_obj

            report_text = self.build_shift_handover_text(
                current_hm, total_sessions, total_apps, sessions, apps, apps_by_session
            )

            await self.bot.send_message(
                tg_http,
                settings.TELEGRAM_GROUP_CHAT_ID,
                report_text,
                message_thread_id=settings.TELEGRAM_TOPIC_ID
            )
            self.last_shift_reported[current_hm] = today_str
            logger.info(f"Shift Handover Report sent for {shift_key} to chat {settings.TELEGRAM_GROUP_CHAT_ID} thread {settings.TELEGRAM_TOPIC_ID}")
