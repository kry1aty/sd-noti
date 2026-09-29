# ServiceDesk Notifier & Watchdog (ELMA365 + Telegram)

Асинхронный сервис мониторинга очереди ServiceDesk в **ELMA365** с Telegram-ботом оповещений и управления сессиями.

## 🚀 Возможности

1. **Watchdog зависших сессий**:
   * Сканирует активные диалоги в «Линиях» ELMA365 (`_lines/_sessions`).
   * Находит сессии в статусе «Назначена на оператора» (`assigned_to_operator`), у которых связанная заявка ServiceDesk уже закрыта/решена дольше заданного времени (`GRACE_PERIOD_MINUTES`).
   * Отправляет оповещение в Telegram-чат/топик с прямой ссылкой на заявку и кнопкой закрытия.

2. **Интерактивное закрытие сессий (RBAC)**:
   * Супервайзеры по deep-link ссылке переходят в личные сообщения бота и могут в один клик принудительно завершить зависшую сессию через API ELMA365.
   * Проверка прав по Telegram User ID и Username.

3. **Отчеты о пересменке**:
   * В назначенное время (по умолчанию 08:00 и 20:00 UTC+5) формирует срез текущей очереди: активные сессии, заявки в работе и в ожидании.
   * Напоминает операторам о сдаче/приеме дежурства.

4. **Ручной аудит**:
   * Команды `/check`, `/status`, `/audit` для супервайзеров в Telegram для мгновенной проверки очереди.

---

## 🛠 Установка и настройка

### 1. Клонирование и зависимости
```bash
git clone https://github.com/kry1aty/sd-noti.git
cd sd-noti

# Установка зависимостей (Python 3.10+)
pip install -r requirements.txt
```

### 2. Настройка переменных окружения
Создайте файл `.env` на основе примера:
```bash
cp .env.example .env
```

Заполните `.env`:
```env
# Подключение к ELMA365
ELMA_BASE_URL=https://your-domain.elma365.ru
ELMA_API_TOKEN=your_elma_bearer_token

# Telegram бот
TELEGRAM_BOT_TOKEN=123456789:ABCdef...
TELEGRAM_GROUP_CHAT_ID=-1001234567890
# Опционально: ID темы (топика) для форумов в супергруппах
TELEGRAM_TOPIC_ID=
BOT_USERNAME=your_bot_username

# Белый список супервайзеров (ID или username через запятую)
SUPERVISOR_USER_IDS=278521888, 875221453
SUPERVISOR_USERNAMES=supervisor_one, supervisor_two

# Тайминги
GRACE_PERIOD_MINUTES=10
CHECK_INTERVAL_SECONDS=20
ALERT_COOLDOWN_MINUTES=60
SHIFT_TIMES=08:00, 20:00
TIMEZONE_OFFSET_HOURS=5
```

---

## ▶ Запуск

```bash
python main.py
```
или через `uv`:
```bash
uv run python main.py
```


