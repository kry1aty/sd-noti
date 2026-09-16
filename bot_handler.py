# -*- coding: utf-8 -*-
"""Telegram Bot Long-Polling & Private Message Action Handler with Topic support."""
import html
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import httpx
from config import settings
from elma_client import ElmaClient

logger = logging.getLogger("sd_notif.bot")


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


def is_ticket_closed(ticket: dict) -> bool:
    if ticket.get("application_completed") is True:
        return True
    ch_st = ticket.get("changing_statuses", {})
    if isinstance(ch_st, dict):
        rows = ch_st.get("rows", [])
        if rows:
            last_code = str(rows[-1].get("status_code", "")).lower()
            last_name = str(rows[-1].get("status_name", "")).lower()
            if last_code in ["decided", "done", "closed", "completed"]:
                return True
            if "выполнен" in last_name or "закрыт" in last_name or "решен" in last_name:
                return True
    return False


class BotHandler:
    def __init__(self, elma: ElmaClient):
        self.elma = elma
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.offset = 0

    def is_supervisor(self, user_id: int, username: Optional[str]) -> bool:
        """Check if user is allowed to perform supervisor actions."""
        if user_id in settings.SUPERVISOR_USER_IDS:
            return True
        if username and username.lower().lstrip("@") in [u.lower().lstrip("@") for u in settings.SUPERVISOR_USERNAMES]:
            return True
        return False

    async def send_message(
        self,
        client: httpx.AsyncClient,
        chat_id: int,
        text: str,
        reply_markup: Optional[dict] = None,
        message_thread_id: Optional[int] = None
    ):
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        # ONLY apply message_thread_id to groups/supergroups (chat_id < 0)
        if chat_id < 0:
            thread_id = message_thread_id or settings.TELEGRAM_TOPIC_ID
            if thread_id:
                payload["message_thread_id"] = thread_id
            
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            r = await client.post(f"{self.base_url}/sendMessage", json=payload)
            if r.status_code != 200:
                logger.error(f"TG Send Error to {chat_id}: {r.status_code} - {r.text}")
        except Exception as e:
            logger.exception(f"Error sending TG message to {chat_id}: {e}")

    async def edit_message(self, client: httpx.AsyncClient, chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None):
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        try:
            await client.post(f"{self.base_url}/editMessageText", json=payload)
        except Exception as e:
            logger.exception(f"Error editing TG message {message_id}: {e}")

    async def answer_callback(self, client: httpx.AsyncClient, callback_id: str, text: str, show_alert: bool = False):
        try:
            await client.post(f"{self.base_url}/answerCallbackQuery", json={
                "callback_query_id": callback_id,
                "text": text,
                "show_alert": show_alert
            })
        except Exception as e:
            logger.exception(f"Error answering callback {callback_id}: {e}")

    async def run_manual_audit(
        self,
        elma_client: httpx.AsyncClient,
        tg_client: httpx.AsyncClient,
        target_chat_id: int,
        message_thread_id: Optional[int] = None
    ):
        """Perform on-demand audit of ELMA365 queue and return report."""
        now = datetime.now(timezone.utc)
        total_sessions, total_apps, sessions, apps = await self.elma.get_totals_and_active(elma_client)

        apps_by_session = {a.get("session_id"): a for a in apps if a.get("session_id")}
        
        assigned_sessions = []
        discrepancies = []

        for s in sessions:
            st = s.get("_state")
            st_code = st[0].get("code") if isinstance(st, list) and st else str(st)
            if st_code == "assigned_to_operator":
                assigned_sessions.append(s)
                sid = s.get("__id")
                ticket = apps_by_session.get(sid)
                if not ticket:
                    s_apps = s.get("_apps") or []
                    if s_apps:
                        t_id = s_apps[-1].get("id")
                        for a in apps:
                            if a.get("__id") == t_id:
                                ticket = a
                                break
                        if not ticket and t_id:
                            ticket = await self.elma.get_application_by_id(elma_client, t_id)

                if ticket and is_ticket_closed(ticket):
                    closed_at_str = ticket.get("__statusChangedAt") or ticket.get("__updatedAt")
                    closed_at = parse_iso(closed_at_str) or now
                    diff_mins = int((now - closed_at).total_seconds() / 60)
                    if diff_mins >= settings.GRACE_PERIOD_MINUTES:
                        discrepancies.append({
                            "session": s,
                            "ticket": ticket,
                            "diff_mins": diff_mins
                        })

        topic_info = f" [Топик ID: {message_thread_id}]" if message_thread_id else ""
        if not discrepancies:
            report = (
                f"🔍 <b>Ручной аудит очереди ELMA365 (по запросу СВ){topic_info}</b>\n\n"
                "✅ <b>Зависших сессий не обнаружено!</b>\n\n"
                f"• <b>Сессий 'Назначена на оператора':</b> {len(assigned_sessions)}\n"
                f"• <b>Активных сессий в Линиях:</b> {len(sessions)} (всего в истории: {total_sessions})\n"
                f"• <b>Всего заявок в ServiceDesk:</b> {total_apps}\n"
                f"• <b>Грейс-период:</b> {settings.GRACE_PERIOD_MINUTES} мин.\n\n"
                "<i>Все сессии с закрытыми заявками своевременно завершены. Очередь чиста. 👍</i>"
            )
            await self.send_message(tg_client, target_chat_id, report, message_thread_id=message_thread_id)
        else:
            report = (
                f"⚠️ <b>Аудит выявил зависших сессий: {len(discrepancies)}!{topic_info}</b>\n\n"
                f"<i>Сессии находятся в статусе 'Назначена на оператора', но связанные заявки уже закрыты более {settings.GRACE_PERIOD_MINUTES} мин назад:</i>\n\n"
            )
            for idx, d in enumerate(discrepancies, 1):
                s = d["session"]
                t = d["ticket"]
                sid = s.get("__id")
                t_name = t.get("__name", "Заявка")
                s_name = s.get("__name", "Сессия")
                diff = d["diff_mins"]
                report += f"<b>{idx}. Заявка {html.escape(t_name)}</b> (Закрыта {diff} мин назад)\n   ├ Сессия: <code>{sid}</code>\n   └ Название: {html.escape(s_name)}\n\n"

            close_first_id = discrepancies[0]["session"].get("__id")
            deep_link = f"https://t.me/{settings.BOT_USERNAME}?start=close_{close_first_id}"
            keyboard = {
                "inline_keyboard": [
                    [{"text": "🔒 Закрыть первую сессию в ЛС", "url": deep_link}]
                ]
            }
            await self.send_message(tg_client, target_chat_id, report, reply_markup=keyboard, message_thread_id=message_thread_id)

    async def handle_message(self, elma_client: httpx.AsyncClient, tg_client: httpx.AsyncClient, message: dict):
        """Handle text commands from users (both private chat and group)."""
        chat = message.get("chat", {})
        user = message.get("from", {})
        chat_id = chat.get("id")
        chat_type = chat.get("type")
        text = message.get("text", "").strip()
        user_id = user.get("id")
        username = user.get("username")
        thread_id = message.get("message_thread_id")

        logger.info(f"Incoming message from {user_id} (@{username}) in chat {chat_id} (thread {thread_id}): '{text}'")

        # Command /check, /status, /audit
        cmd = text.split("@")[0].lower()
        if cmd in ["/check", "/status", "/audit", "/scan", "/check@" + settings.BOT_USERNAME.lower()]:
            if not self.is_supervisor(user_id, username):
                if chat_type == "private":
                    await self.send_message(
                        tg_client,
                        chat_id,
                        "⛔ <b>Доступ ограничен</b>\n\nКоманда аудита доступна только супервайзерам ServiceDesk.",
                        message_thread_id=thread_id
                    )
                return

            await self.send_message(
                tg_client,
                chat_id,
                "⏳ <i>Выполняю аудит сессий и заявок в ELMA365...</i>",
                message_thread_id=thread_id
            )
            await self.run_manual_audit(elma_client, tg_client, chat_id, message_thread_id=thread_id)
            return

        # PM Only handling for /start
        if chat_type == "private":
            if text.startswith("/start close_"):
                session_id = text.replace("/start close_", "").strip()
                
                if not self.is_supervisor(user_id, username):
                    await self.send_message(
                        tg_client,
                        chat_id,
                        "⛔ <b>Доступ ограничен</b>\n\nЗакрытие сессий через бота разрешено только супервайзерам и администраторам ServiceDesk."
                    )
                    return

                session_data = await self.elma.get_session_by_id(elma_client, session_id)
                sess_name = session_data.get("__name", "Сессия") if session_data else "Сессия"
                sess_state = "Открыта"
                if session_data:
                    st = session_data.get("_state")
                    if isinstance(st, list) and st:
                        sess_state = st[0].get("name", "Открыта")

                card = (
                    f"👑 <b>Панель супервайзера: Закрытие сессии</b>\n\n"
                    f"💬 <b>Сессия:</b> <code>{session_id}</code>\n"
                    f"🏷 <b>Название:</b> {html.escape(sess_name)}\n"
                    f"📊 <b>Текущий статус:</b> <i>{html.escape(sess_state)}</i>\n\n"
                    f"Вы действительно хотите принудительно завершить данную сессию в ELMA365?"
                )
                keyboard = {
                    "inline_keyboard": [
                        [
                            {"text": "🔒 Да, закрыть сессию", "callback_data": f"do_close:{session_id}"},
                            {"text": "❌ Отмена", "callback_data": "cancel_close"}
                        ]
                    ]
                }
                await self.send_message(tg_client, chat_id, card, reply_markup=keyboard)
            else:
                is_sup = self.is_supervisor(user_id, username)
                role_badge = "👑 Супервайзер" if is_sup else "👤 Оператор"
                welcome = (
                    f"👋 Приветствуем в <b>ServiceDesk Notifier Bot</b>!\n\n"
                    f"Ваша роль: <b>{role_badge}</b>\n\n"
                    f"<b>Доступные команды:</b>\n"
                    f"• <code>/check</code> — провести мгновенный аудит зависших сессий в ELMA365\n"
                    f"• <code>/status</code> — статус очереди и пересменки"
                )
                await self.send_message(tg_client, chat_id, welcome)

    async def handle_callback_query(self, elma_client: httpx.AsyncClient, tg_client: httpx.AsyncClient, query: dict):
        """Handle interactive buttons in private chat."""
        q_id = query.get("id")
        user = query.get("from", {})
        user_id = user.get("id")
        username = user.get("username", user.get("first_name", "Unknown"))
        data = query.get("data", "")
        msg = query.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        msg_id = msg.get("message_id")

        if data == "cancel_close":
            await self.answer_callback(tg_client, q_id, "Действие отменено")
            await self.edit_message(tg_client, chat_id, msg_id, "❌ Закрытие сессии отменено.")
            return

        if data.startswith("do_close:"):
            session_id = data.replace("do_close:", "").strip()
            
            if not self.is_supervisor(user_id, user.get("username")):
                await self.answer_callback(tg_client, q_id, "⛔ У вас нет прав супервайзера!", show_alert=True)
                return

            success = await self.elma.close_session(elma_client, session_id)
            if success:
                await self.answer_callback(tg_client, q_id, "✅ Сессия успешно закрыта!")
                await self.edit_message(
                    tg_client,
                    chat_id,
                    msg_id,
                    f"✅ <b>Сессия закрыта</b>\n\nID: <code>{session_id}</code> успешно переведена в статус <i>Закрыта</i> в ELMA365."
                )
                user_mention = f"@{username}" if user.get("username") else f"{user.get('first_name')}"
                group_note = (
                    f"🔒 <b>Сессия закрыта через бота</b>\n\n"
                    f"• <b>Сессия:</b> <code>{session_id}</code>\n"
                    f"• <b>Супервайзер:</b> {user_mention}\n"
                    f"• <b>Статус в ELMA365:</b> <i>Закрыта</i>"
                )
                await self.send_message(tg_client, settings.TELEGRAM_GROUP_CHAT_ID, group_note)
            else:
                await self.answer_callback(tg_client, q_id, "❌ Ошибка при закрытии сессии в ELMA365", show_alert=True)

    async def poll_updates(self, elma_client: httpx.AsyncClient, tg_client: httpx.AsyncClient):
        """Poll for Telegram updates."""
        try:
            url = f"{self.base_url}/getUpdates"
            resp = await tg_client.get(url, params={"offset": self.offset, "timeout": 5})
            if resp.status_code == 200:
                updates = resp.json().get("result", [])
                for u in updates:
                    self.offset = u.get("update_id", 0) + 1
                    if "message" in u:
                        await self.handle_message(elma_client, tg_client, u["message"])
                    elif "callback_query" in u:
                        await self.handle_callback_query(elma_client, tg_client, u["callback_query"])
        except Exception as e:
            logger.debug(f"TG poll cycle error: {e}")
