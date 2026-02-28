import logging
import os
import re
import signal
import sqlite3
import json
import importlib
import uuid
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()

DB_PATH = os.getenv("TASK_BOT_DB_PATH", "tasks.db")
OWNER_USER_ID = os.getenv("OWNER_USER_ID")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT_SECONDS = 5
OLLAMA_MODULE_CACHE = None

INTENT_VALUES = {
    "CREATE_TASK",
    "CREATE_NOTE",
    "SAVE_IMPORTANT",
    "LIST_ALL_TASKS",
    "LIST_TODAY",
    "LIST_TOMORROW",
    "LIST_WEEK",
    "SHOW_NOTES",
    "SHOW_IMPORTANT_NOTES",
    "OVERVIEW_ALL",
    "UPCOMING_REMINDERS",
    "SEARCH",
    "UNKNOWN",
}

CREATE_INTENTS = {"CREATE_TASK", "CREATE_NOTE", "SAVE_IMPORTANT"}

WEEKDAY_MAP = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

DEFAULT_AI_RESULT = {
    "intent": "UNKNOWN",
    "title": "",
    "content": "",
    "due_date": None,
    "due_time": None,
    "event_at": None,
    "search_query": "",
    "confidence": "low",
}


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def get_ollama_module():
    global OLLAMA_MODULE_CACHE
    if OLLAMA_MODULE_CACHE is not None:
        return OLLAMA_MODULE_CACHE
    try:
        OLLAMA_MODULE_CACHE = importlib.import_module("ollama")
    except Exception:
        OLLAMA_MODULE_CACHE = False
    return OLLAMA_MODULE_CACHE if OLLAMA_MODULE_CACHE is not False else None


def init_db() -> None:
    with get_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                due_date TEXT,
                due_time TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                updated_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_main_message (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        migrate_schema(connection)


def migrate_schema(connection: sqlite3.Connection) -> None:
    task_columns = get_table_columns(connection, "tasks")
    if "due_date" not in task_columns:
        connection.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")
    if "due_time" not in task_columns:
        connection.execute("ALTER TABLE tasks ADD COLUMN due_time TEXT")
    if "updated_at" not in task_columns:
        connection.execute("ALTER TABLE tasks ADD COLUMN updated_at TEXT")
    if "due_at" in task_columns:
        connection.execute(
            """
            UPDATE tasks
            SET due_date = substr(due_at, 1, 10)
            WHERE (due_date IS NULL OR due_date = '') AND due_at IS NOT NULL
            """
        )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            event_at TEXT,
            is_important INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            alert_interval_seconds INTEGER NOT NULL DEFAULT 45,
            alert_max_repeats INTEGER NOT NULL DEFAULT 8,
            quiet_hours_start TEXT DEFAULT '23:00',
            quiet_hours_end TEXT DEFAULT '07:00',
            auto_reseat INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    user_settings_columns = get_table_columns(connection, "user_settings")
    if "auto_reseat" not in user_settings_columns:
        connection.execute("ALTER TABLE user_settings ADD COLUMN auto_reseat INTEGER NOT NULL DEFAULT 0")


def get_table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def get_tasks_due_date_expression(task_columns: set[str]) -> str:
    if "due_date" in task_columns and "due_at" in task_columns:
        return "COALESCE(due_date, substr(due_at, 1, 10))"
    if "due_date" in task_columns:
        return "due_date"
    if "due_at" in task_columns:
        return "substr(due_at, 1, 10)"
    return "NULL"


def get_tasks_due_time_expression(task_columns: set[str]) -> str:
    if "due_time" in task_columns:
        return "due_time"
    if "due_at" in task_columns:
        return "substr(due_at, 12, 5)"
    return "NULL"


def set_user_setting_defaults_if_missing(user_id: int) -> None:
    with get_db() as connection:
        connection.execute(
            """
            INSERT INTO user_settings (user_id)
            VALUES (?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id,),
        )


def get_user_auto_reseat(user_id: int) -> bool:
    with get_db() as connection:
        row = connection.execute(
            "SELECT auto_reseat FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return False
    value = row["auto_reseat"] if "auto_reseat" in row.keys() else 0
    return int(value or 0) == 1


def add_note(
    user_id: int,
    content: str,
    event_at: Optional[str] = None,
    is_important: bool = False,
) -> int:
    with get_db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO notes (user_id, content, event_at, is_important)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, content.strip(), event_at, 1 if is_important else 0),
        )
        return int(cursor.lastrowid)


def get_recent_notes(user_id: int, limit: int = 10, offset: int = 0) -> list[sqlite3.Row]:
    with get_db() as connection:
        return connection.execute(
            """
            SELECT id, content, event_at, is_important, created_at
            FROM notes
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()


def get_important_notes(user_id: int, limit: int = 10, offset: int = 0) -> list[sqlite3.Row]:
    with get_db() as connection:
        return connection.execute(
            """
            SELECT id, content, event_at, is_important, created_at
            FROM notes
            WHERE user_id = ? AND is_important = 1
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()


def delete_note(user_id: int, note_id: int) -> bool:
    with get_db() as connection:
        result = connection.execute(
            """
            DELETE FROM notes
            WHERE id = ? AND user_id = ?
            """,
            (note_id, user_id),
        )
        return result.rowcount > 0


def toggle_note_important(user_id: int, note_id: int) -> bool:
    with get_db() as connection:
        result = connection.execute(
            """
            UPDATE notes
            SET is_important = CASE WHEN is_important = 1 THEN 0 ELSE 1 END
            WHERE id = ? AND user_id = ?
            """,
            (note_id, user_id),
        )
        return result.rowcount > 0


def update_note_content(user_id: int, note_id: int, content: str) -> bool:
    with get_db() as connection:
        result = connection.execute(
            """
            UPDATE notes
            SET content = ?
            WHERE id = ? AND user_id = ?
            """,
            (content.strip(), note_id, user_id),
        )
        return result.rowcount > 0


def get_active_tasks_for_due_date(user_id: int, due_date: str) -> list[sqlite3.Row]:
    with get_db() as connection:
        task_columns = get_table_columns(connection, "tasks")
        due_expr = get_tasks_due_date_expression(task_columns)
        return connection.execute(
            f"""
            SELECT id,
                   description,
                   {due_expr} AS due_date
            FROM tasks
            WHERE user_id = ? AND status = 'active' AND {due_expr} = ?
            ORDER BY id ASC
            """,
            (user_id, due_date),
        ).fetchall()


def get_tasks_due_range(user_id: int, start_date: str, end_date: str) -> list[sqlite3.Row]:
    with get_db() as connection:
        task_columns = get_table_columns(connection, "tasks")
        due_expr = get_tasks_due_date_expression(task_columns)
        due_time_expr = get_tasks_due_time_expression(task_columns)
        return connection.execute(
            f"""
            SELECT id,
                   description,
                   {due_expr} AS due_date,
                   {due_time_expr} AS due_time
            FROM tasks
            WHERE user_id = ?
              AND status = 'active'
              AND {due_expr} IS NOT NULL
              AND {due_expr} >= ?
              AND {due_expr} <= ?
            ORDER BY {due_expr} ASC, id ASC
            """,
            (user_id, start_date, end_date),
        ).fetchall()


def get_active_reminders_flat(user_id: int) -> list[sqlite3.Row]:
    with get_db() as connection:
        task_columns = get_table_columns(connection, "tasks")
        due_expr = get_tasks_due_date_expression(task_columns)
        due_time_expr = get_tasks_due_time_expression(task_columns)
        return connection.execute(
            f"""
            SELECT id,
                   description,
                   {due_expr} AS due_date,
                   {due_time_expr} AS due_time
            FROM tasks
            WHERE user_id = ?
              AND status = 'active'
            ORDER BY
                CASE WHEN {due_expr} IS NULL OR {due_expr} = '' THEN 1 ELSE 0 END ASC,
                {due_expr} ASC,
                CASE WHEN {due_time_expr} IS NULL OR {due_time_expr} = '' THEN 1 ELSE 0 END ASC,
                {due_time_expr} ASC,
                id ASC
            """,
            (user_id,),
        ).fetchall()


def get_overdue_tasks(user_id: int) -> list[sqlite3.Row]:
    with get_db() as connection:
        task_columns = get_table_columns(connection, "tasks")
        due_expr = get_tasks_due_date_expression(task_columns)
        today_date = datetime.now().strftime("%Y-%m-%d")
        return connection.execute(
            f"""
            SELECT id,
                   description,
                   {due_expr} AS due_date
            FROM tasks
            WHERE user_id = ?
              AND status = 'active'
              AND {due_expr} IS NOT NULL
              AND {due_expr} < ?
            ORDER BY {due_expr} ASC, id ASC
            """,
            (user_id, today_date),
        ).fetchall()


def get_pending_actions_store(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if "pending_actions" not in context.user_data:
        context.user_data["pending_actions"] = {}
    return context.user_data["pending_actions"]


def create_pending_id() -> str:
    return uuid.uuid4().hex[:8]


def default_ai_result() -> dict:
    return DEFAULT_AI_RESULT.copy()


def validate_ai_result(raw: object) -> dict:
    result = default_ai_result()
    if not isinstance(raw, dict):
        return result

    intent = str(raw.get("intent", "UNKNOWN")).strip().upper()
    if intent in INTENT_VALUES:
        result["intent"] = intent

    for key in ("title", "content", "search_query"):
        value = raw.get(key)
        if isinstance(value, str):
            result[key] = value.strip()

    due_date = raw.get("due_date")
    if isinstance(due_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_date):
        try:
            datetime.strptime(due_date, "%Y-%m-%d")
            result["due_date"] = due_date
        except ValueError:
            pass

    due_time = raw.get("due_time")
    if isinstance(due_time, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", due_time):
        result["due_time"] = due_time

    event_at = raw.get("event_at")
    if isinstance(event_at, str):
        try:
            datetime.fromisoformat(event_at.replace("Z", "+00:00"))
            result["event_at"] = event_at
        except ValueError:
            pass

    confidence = str(raw.get("confidence", "low")).strip().lower()
    if confidence in {"high", "medium", "low"}:
        result["confidence"] = confidence

    return result


def extract_first_json_object(raw_text: str) -> Optional[dict]:
    match = re.search(r"\{[\s\S]*\}", raw_text)
    if not match:
        return None
    try:
        loaded = json.loads(match.group(0))
        if isinstance(loaded, dict):
            return loaded
        return None
    except json.JSONDecodeError:
        return None


def call_ollama(text: str, now_iso: str) -> Optional[str]:
    prompt = (
        "Return only strict JSON without markdown or prose. "
        "Infer user intent and fields. "
        "Current datetime: "
        f"{now_iso}.\n"
        "JSON schema keys required: intent,title,content,due_date,due_time,event_at,search_query,confidence.\n"
        "intent must be one of: CREATE_TASK,CREATE_NOTE,SAVE_IMPORTANT,LIST_ALL_TASKS,LIST_TODAY,"
        "LIST_TOMORROW,LIST_WEEK,SHOW_NOTES,SHOW_IMPORTANT_NOTES,OVERVIEW_ALL,SEARCH,UNKNOWN.\n"
        "due_date format YYYY-MM-DD or null. due_time format HH:MM or null. event_at ISO datetime or null.\n"
        "confidence one of high,medium,low.\n"
        f"User text: {text}"
    )

    try:
        ollama_module = importlib.import_module("ollama")
        Client = getattr(ollama_module, "Client")
        client = Client(host=OLLAMA_HOST, timeout=OLLAMA_TIMEOUT_SECONDS)
        response = client.generate(
            model=OLLAMA_MODEL,
            prompt=prompt,
            options={"temperature": 0},
        )
        response_text = response.get("response", "") if isinstance(response, dict) else ""
        if isinstance(response_text, str) and response_text.strip():
            logger.info("Ollama package response received")
            return response_text
    except Exception as error:
        logger.warning("Ollama package call failed: %s", error)

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{OLLAMA_HOST}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
            response_text = response_payload.get("response", "")
            if isinstance(response_text, str) and response_text.strip():
                logger.info("Ollama HTTP response received")
                return response_text
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        logger.warning("Ollama HTTP call failed: %s", error)
    except Exception as error:
        logger.warning("Unexpected Ollama HTTP failure: %s", error)

    return None


def ai_parse_message(text: str, now: datetime) -> dict:
    now_iso = now.replace(microsecond=0).isoformat()
    raw = call_ollama(text, now_iso)
    if not raw:
        return default_ai_result()

    parsed = extract_first_json_object(raw)
    if parsed is None:
        return default_ai_result()

    validated = validate_ai_result(parsed)
    if validated["intent"] == "UNKNOWN":
        logger.info("AI parse returned UNKNOWN or invalid schema")
    return validated


def detect_rule_intent(text: str) -> Optional[dict]:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if not normalized:
        return None

    query_starts = (
        "show",
        "list",
        "what",
        "whats",
        "what's",
        "display",
        "give me",
        "can you show",
    )
    query_contains = (
        "my tasks",
        "my notes",
        "overview",
        "all my",
        "due today",
        "due tomorrow",
    )
    is_query_like = normalized.startswith(query_starts) or any(part in normalized for part in query_contains)
    if not is_query_like:
        return None

    if normalized in {"show all my tasks", "show all my task", "all tasks", "list tasks"} or "show all my tasks" in normalized:
        return {"intent": "LIST_ALL_TASKS", "source": "rule"}
    if normalized in {"show all my note", "show all my notes", "list notes"} or normalized.startswith("notes") or normalized.startswith("show notes"):
        return {"intent": "SHOW_NOTES", "source": "rule"}
    if "important notes" in normalized:
        return {"intent": "SHOW_IMPORTANT_NOTES", "source": "rule"}
    if "show my reminders" in normalized or "upcoming reminders" in normalized:
        return {"intent": "UPCOMING_REMINDERS", "source": "rule"}
    if "overview" in normalized or normalized in {"show everything", "show all my data"}:
        return {"intent": "OVERVIEW_ALL", "source": "rule"}
    if "tomorrow" in normalized or "tmr" in normalized:
        return {"intent": "LIST_TOMORROW", "source": "rule"}
    if "today" in normalized:
        return {"intent": "LIST_TODAY", "source": "rule"}
    return None


def looks_like_temporal_capture(text: str) -> bool:
    normalized = text.strip().lower()
    if re.search(r"\b(tmr|tomorrow|today|tonight)\b", normalized):
        return True
    if re.search(r"\b\d{1,2}(:\d{2})?\s?(am|pm)\b", normalized):
        return True
    if re.search(r"\b\d{1,2}:\d{2}\b", normalized):
        return True
    return False


def format_recent_notes_text(notes: list[sqlite3.Row]) -> str:
    if not notes:
        return "🗒️ Recent Notes\nNo notes yet."

    lines = ["🗒️ Recent Notes"]
    for note in notes:
        important = "⭐ " if note["is_important"] else ""
        event_suffix = f" (event: {note['event_at']})" if note["event_at"] else ""
        lines.append(f"#{note['id']} {important}- {note['content']}{event_suffix}")
    return "\n".join(lines)


def build_recent_notes_keyboard(notes: list[sqlite3.Row]) -> Optional[InlineKeyboardMarkup]:
    if not notes:
        return None

    keyboard: list[list[InlineKeyboardButton]] = []
    for note in notes:
        note_id = int(note["id"])
        star_label = "⭐ Toggle important" if not note["is_important"] else "⭐ Unmark important"
        keyboard.append(
            [
                InlineKeyboardButton(f"🗑️ Delete #{note_id}", callback_data=f"note_delete:{note_id}"),
                InlineKeyboardButton(star_label, callback_data=f"note_star:{note_id}"),
            ]
        )
        keyboard.append(
            [InlineKeyboardButton(f"✏️ Edit #{note_id}", callback_data=f"note_edit:{note_id}")]
        )
    return InlineKeyboardMarkup(keyboard)


def add_task(
    user_id: int,
    chat_id: int,
    description: str,
    due_date: Optional[str],
    due_time: Optional[str] = None,
) -> int:
    with get_db() as connection:
        task_columns = get_table_columns(connection, "tasks")
        fields = ["user_id", "description", "due_date"]
        values = [user_id, description.strip(), due_date]

        if "chat_id" in task_columns:
            fields.append("chat_id")
            values.append(chat_id)

        if "due_at" in task_columns:
            if due_date:
                due_at = f"{due_date}T18:00:00"
            else:
                due_at = datetime.now().replace(microsecond=0).isoformat()
            fields.append("due_at")
            values.append(due_at)

        if "updated_at" in task_columns:
            fields.append("updated_at")
            values.append(datetime.now().replace(microsecond=0).isoformat())

        if "due_time" in task_columns:
            fields.append("due_time")
            values.append(due_time)

        cursor = connection.execute(
            f"INSERT INTO tasks ({', '.join(fields)}) VALUES ({', '.join(['?'] * len(fields))})",
            values,
        )
        return int(cursor.lastrowid)


def get_active_tasks(user_id: int) -> list[sqlite3.Row]:
    with get_db() as connection:
        task_columns = get_table_columns(connection, "tasks")
        due_expr = get_tasks_due_date_expression(task_columns)
        return connection.execute(
            f"""
            SELECT id,
                   description,
                   {due_expr} AS due_date
            FROM tasks
            WHERE user_id = ? AND status = 'active'
            ORDER BY id ASC
            """,
            (user_id,),
        ).fetchall()


def get_done_tasks(user_id: int) -> list[sqlite3.Row]:
    with get_db() as connection:
        task_columns = get_table_columns(connection, "tasks")
        due_expr = get_tasks_due_date_expression(task_columns)
        return connection.execute(
            f"""
            SELECT id,
                   description,
                   {due_expr} AS due_date
            FROM tasks
            WHERE user_id = ? AND status = 'done'
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()


def mark_task_done(user_id: int, task_id: int) -> bool:
    with get_db() as connection:
        task_columns = get_table_columns(connection, "tasks")
        if "updated_at" in task_columns:
            result = connection.execute(
                """
                UPDATE tasks
                SET status = 'done', updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND status = 'active'
                """,
                (task_id, user_id),
            )
        else:
            result = connection.execute(
                """
                UPDATE tasks
                SET status = 'done'
                WHERE id = ? AND user_id = ? AND status = 'active'
                """,
                (task_id, user_id),
            )
        return result.rowcount > 0


def clear_done_tasks(user_id: int) -> int:
    with get_db() as connection:
        result = connection.execute(
            "DELETE FROM tasks WHERE user_id = ? AND status = 'done'",
            (user_id,),
        )
        return result.rowcount


def save_main_message_ref(user_id: int, chat_id: int, message_id: int) -> None:
    with get_db() as connection:
        connection.execute(
            """
            INSERT INTO user_main_message (user_id, chat_id, message_id, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id)
            DO UPDATE SET
                chat_id = excluded.chat_id,
                message_id = excluded.message_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, chat_id, message_id),
        )


def get_main_message_ref(user_id: int) -> Optional[sqlite3.Row]:
    with get_db() as connection:
        return connection.execute(
            "SELECT chat_id, message_id FROM user_main_message WHERE user_id = ?",
            (user_id,),
        ).fetchone()


def get_known_users() -> list[sqlite3.Row]:
    with get_db() as connection:
        return connection.execute(
            "SELECT user_id, chat_id FROM user_main_message"
        ).fetchall()


def parse_due_date(raw_text: str) -> tuple[Optional[str], Optional[str]]:
    value = raw_text.strip()
    if value == "0":
        return None, None
    if value == "00":
        return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"), None

    today = datetime.now()

    if re.fullmatch(r"\d{1,2}", value):
        day = int(value)
        try:
            parsed = datetime(today.year, today.month, day)
            return parsed.strftime("%Y-%m-%d"), None
        except ValueError:
            return None, "Invalid day for current month."

    cleaned = re.sub(r"[.\\-]", "/", value)

    if re.fullmatch(r"\d{1,2}/\d{1,2}", cleaned):
        day_str, month_str = cleaned.split("/")
        day = int(day_str)
        month = int(month_str)
        try:
            parsed = datetime(today.year, month, day)
            return parsed.strftime("%Y-%m-%d"), None
        except ValueError:
            return None, "Invalid day/month combination."

    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", cleaned):
        day_str, month_str, year_str = cleaned.split("/")
        day = int(day_str)
        month = int(month_str)
        year = int(year_str)
        if len(year_str) == 2:
            year += 2000
        try:
            parsed = datetime(year, month, day)
            return parsed.strftime("%Y-%m-%d"), None
        except ValueError:
            return None, "Invalid date."

    return None, "Use 0, 00, DD, DD/MM, or DD/MM/YYYY"


def parse_time_24h_from_text(text: str) -> Optional[str]:
    normalized = text.lower()
    am_pm_match = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b", normalized)
    if am_pm_match:
        hour = int(am_pm_match.group(1))
        minute = int(am_pm_match.group(2) or "0")
        am_pm = am_pm_match.group(3)
        if am_pm == "pm" and hour != 12:
            hour += 12
        if am_pm == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    h24_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", normalized)
    if h24_match:
        return f"{int(h24_match.group(1)):02d}:{int(h24_match.group(2)):02d}"

    return None


def parse_weekday_from_text(text: str) -> Optional[int]:
    normalized = text.lower()
    for label, weekday in WEEKDAY_MAP.items():
        if re.search(rf"\b{label}\b", normalized):
            return weekday
    return None


def next_week_monday(base_date: datetime) -> datetime:
    days_until_next_monday = 7 - base_date.weekday()
    if days_until_next_monday <= 0:
        days_until_next_monday += 7
    return base_date + timedelta(days=days_until_next_monday)


def parse_date_from_text(text: str, now: Optional[datetime] = None) -> Optional[str]:
    base = now or datetime.now()
    normalized = text.lower().strip()

    full_date_match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", normalized)
    if full_date_match:
        day = int(full_date_match.group(1))
        month = int(full_date_match.group(2))
        year = int(full_date_match.group(3))
        if year < 100:
            year += 2000
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    day_month_match = re.search(r"\b(\d{1,2})[./-](\d{1,2})\b", normalized)
    if day_month_match:
        day = int(day_month_match.group(1))
        month = int(day_month_match.group(2))
        try:
            return datetime(base.year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    day_only_match = None
    if re.fullmatch(r"\d{1,2}", normalized):
        day_only_match = re.match(r"(\d{1,2})", normalized)
    else:
        day_only_match = re.search(r"\bon\s+(\d{1,2})\b", normalized)
    if day_only_match:
        day = int(day_only_match.group(1))
        try:
            return datetime(base.year, base.month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    if re.search(r"\btoday\b", normalized):
        return base.strftime("%Y-%m-%d")
    if re.search(r"\b(tomorrow|tmr)\b", normalized):
        return (base + timedelta(days=1)).strftime("%Y-%m-%d")
    if re.search(r"\btonight\b", normalized):
        return base.strftime("%Y-%m-%d")

    if re.search(r"\bnext\s+year\b", normalized):
        try:
            return datetime(base.year + 1, base.month, base.day).strftime("%Y-%m-%d")
        except ValueError:
            return datetime(base.year + 1, 12, 31).strftime("%Y-%m-%d")

    if re.search(r"\bnext\s+week\b", normalized):
        weekday = parse_weekday_from_text(normalized)
        monday = next_week_monday(base)
        if weekday is None:
            return monday.strftime("%Y-%m-%d")
        return (monday + timedelta(days=(weekday - 0))).strftime("%Y-%m-%d")

    weekday = parse_weekday_from_text(normalized)
    if weekday is not None:
        days_ahead = (weekday - base.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return (base + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    return None


def parse_local_reminder_datetime(text: str, now: Optional[datetime] = None) -> tuple[Optional[str], Optional[str]]:
    due_date = parse_date_from_text(text, now=now)
    due_time = parse_time_24h_from_text(text)
    return due_date, due_time


def strip_prefix(text: str, prefix: str) -> str:
    return re.sub(rf"^\s*{prefix}\s*:?\s*", "", text, flags=re.IGNORECASE).strip()


def looks_like_reminder_language(text: str) -> bool:
    normalized = text.lower()
    reminder_keywords = [
        "remind",
        "reminder",
        "due",
        "today",
        "tomorrow",
        "tmr",
        "tonight",
        "next week",
        "next year",
        "am",
        "pm",
    ]
    if any(keyword in normalized for keyword in reminder_keywords):
        return True
    if parse_time_24h_from_text(text):
        return True
    if parse_date_from_text(text):
        return True
    return False


def call_ollama_note_or_reminder(text: str, now_iso: str) -> Optional[dict]:
    prompt = (
        "Return only strict JSON without markdown or prose. "
        "Classify the user message into NOTE or REMINDER and extract concise text fields. "
        f"Current datetime: {now_iso}.\n"
        "JSON schema keys required: intent,title,content,confidence.\n"
        "intent must be NOTE or REMINDER. confidence must be high,medium,low.\n"
        f"User text: {text}"
    )

    raw_response: Optional[str] = None

    try:
        ollama_module = get_ollama_module()
        if ollama_module is not None:
            client = ollama_module.Client(host=OLLAMA_HOST, timeout=OLLAMA_TIMEOUT_SECONDS)
            response = client.generate(
                model=OLLAMA_MODEL,
                prompt=prompt,
                options={"temperature": 0},
            )
            response_text = response.get("response", "") if isinstance(response, dict) else ""
            if isinstance(response_text, str) and response_text.strip():
                raw_response = response_text
    except Exception as error:
        logger.warning("Ollama NOTE/REMINDER package call failed: %s", error)

    if not raw_response:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{OLLAMA_HOST}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
                response_text = response_payload.get("response", "")
                if isinstance(response_text, str) and response_text.strip():
                    raw_response = response_text
        except Exception as error:
            logger.warning("Ollama NOTE/REMINDER HTTP call failed: %s", error)

    if not raw_response:
        return None

    parsed = extract_first_json_object(raw_response)
    if not parsed:
        return None

    intent = str(parsed.get("intent", "")).strip().upper()
    if intent not in {"NOTE", "REMINDER"}:
        return None

    title = parsed.get("title") if isinstance(parsed.get("title"), str) else ""
    content = parsed.get("content") if isinstance(parsed.get("content"), str) else ""
    confidence = str(parsed.get("confidence", "low")).strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    return {
        "intent": intent,
        "title": title.strip(),
        "content": content.strip(),
        "confidence": confidence,
    }


def classify_note_or_reminder(text: str, now: Optional[datetime] = None) -> dict:
    now_dt = now or datetime.now()
    now_iso = now_dt.replace(microsecond=0).isoformat()
    ai_result = call_ollama_note_or_reminder(text, now_iso)
    if ai_result:
        return ai_result

    fallback_intent = "REMINDER" if looks_like_reminder_language(text) else "NOTE"
    return {
        "intent": fallback_intent,
        "title": "",
        "content": text.strip(),
        "confidence": "low",
    }


def track_message(context: ContextTypes.DEFAULT_TYPE, user_id: int, message_id: int) -> None:
    """Track a bot message ID for later deletion."""
    if "tracked_messages" not in context.user_data:
        context.user_data["tracked_messages"] = set()
    context.user_data["tracked_messages"].add(message_id)


def get_tracked_messages(context: ContextTypes.DEFAULT_TYPE) -> set:
    """Get all tracked message IDs."""
    return context.user_data.get("tracked_messages", set())


def clear_tracked_messages(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear all tracked message IDs."""
    context.user_data["tracked_messages"] = set()


async def clear_temporary_messages(context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int) -> int:
    """Delete all tracked temporary messages and return count."""
    tracked_ids = get_tracked_messages(context)
    deleted = 0
    for msg_id in tracked_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted += 1
        except Exception as e:
            logger.debug(f"Could not delete temp message {msg_id}: {e}")
    clear_tracked_messages(context)
    return deleted


def due_with_day_label(due_date: Optional[str]) -> str:
    if not due_date:
        return "No due date"
    parsed = datetime.strptime(due_date, "%Y-%m-%d")
    suffix = ""
    if parsed.date() == (datetime.now() + timedelta(days=1)).date():
        suffix = ", tmr"
    return f"{due_date} ({parsed.strftime('%a')}{suffix})"


def format_task_list(tasks: list[sqlite3.Row], checkbox: str) -> str:
    if not tasks:
        return "No tasks."
    return "\n".join(
        f"{checkbox} {task['description']} — {due_with_day_label(task['due_date'])}"
        for task in tasks
    )


def shorten_text(value: str, max_len: int = 52) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def get_panel_state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    if "panel_state" not in context.user_data:
        context.user_data["panel_state"] = {
            "mode": None,
            "page": 0,
            "show_done": False,
            "message_id": None,
            "chat_id": None,
        }
    return context.user_data["panel_state"]


def is_in_edit_panel(context: ContextTypes.DEFAULT_TYPE) -> bool:
    panel_state = get_panel_state(context)
    return panel_state.get("mode") in {"notes", "reminders"}


def format_note_line(index: int, note: sqlite3.Row) -> str:
    important = "⭐ " if note["is_important"] else ""
    return f"- {index}. {important}{note['content']}"


def format_display_date(date_value: Optional[str]) -> Optional[str]:
    if not date_value:
        return None
    try:
        parsed = datetime.strptime(date_value, "%Y-%m-%d")
        return parsed.strftime("%d/%m/%Y")
    except ValueError:
        return date_value


def format_reminder_due_inline(task: sqlite3.Row) -> str:
    due_date = task["due_date"] if "due_date" in task.keys() else None
    due_time = task["due_time"] if "due_time" in task.keys() else None
    display_date = format_display_date(due_date)
    due_bits = [bit for bit in [due_time, display_date] if bit]
    if not due_bits:
        return ""
    return f" ({' '.join(due_bits)})"


def format_reminder_line(index: int, task: sqlite3.Row) -> str:
    return f"- {index}. {task['description']}{format_reminder_due_inline(task)}"


def build_note_panel_text(user_id: int, page: int) -> str:
    safe_page = max(page, 0)
    page_size = 8
    offset = safe_page * page_size
    notes = get_recent_notes(user_id, limit=page_size, offset=offset)
    lines = [f"Edit Note (Page {safe_page + 1})"]
    if not notes:
        lines.append("No notes found.")
    else:
        for idx, note in enumerate(notes, start=offset + 1):
            lines.append(format_note_line(idx, note))
    return "\n".join(lines)


def build_note_panel_keyboard(user_id: int, page: int) -> InlineKeyboardMarkup:
    safe_page = max(page, 0)
    page_size = 8
    offset = safe_page * page_size
    notes = get_recent_notes(user_id, limit=page_size + 1, offset=offset)
    notes_page = notes[:page_size]
    has_prev = safe_page > 0
    has_next = len(notes) > page_size

    keyboard: list[list[InlineKeyboardButton]] = []
    for note in notes_page:
        note_id = int(note["id"])
        star_label = "⭐ Unmark" if note["is_important"] else "⭐ Mark"
        keyboard.append(
            [
                InlineKeyboardButton(f"✏️ Edit #{note_id}", callback_data=f"note_edit:{note_id}"),
                InlineKeyboardButton(f"🗑️ Delete #{note_id}", callback_data=f"note_delete:{note_id}"),
                InlineKeyboardButton(star_label, callback_data=f"note_star:{note_id}"),
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if has_prev:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"panel_notes_page:{safe_page - 1}"))
    if has_next:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"panel_notes_page:{safe_page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("➕ Add Note", callback_data="add_note")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="panel_back")])
    return InlineKeyboardMarkup(keyboard)


def build_reminder_panel_text(user_id: int, show_done: bool = False) -> str:
    active = get_active_reminders_flat(user_id)
    lines = ["Edit Reminder"]
    lines.append("\nActive Reminders:")
    if not active:
        lines.append("- None")
    else:
        for idx, task in enumerate(active, start=1):
            lines.append(format_reminder_line(idx, task))

    if show_done:
        done_tasks = get_done_tasks(user_id)
        lines.append("\nDone Reminders:")
        if not done_tasks:
            lines.append("- None")
        else:
            for idx, task in enumerate(done_tasks, start=1):
                formatted_done_date = format_display_date(task["due_date"])
                done_due = f" ({formatted_done_date})" if formatted_done_date else ""
                lines.append(f"- {idx}. {task['description']}{done_due}")

    return "\n".join(lines)


def build_reminder_panel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    for idx, task in enumerate(get_active_reminders_flat(user_id), start=1):
        keyboard.append(
            [InlineKeyboardButton(f"☐ Tick {idx}", callback_data=f"done:{task['id']}")]
        )

    keyboard.append([InlineKeyboardButton("📋 Show Done Reminders", callback_data="show_done")])
    keyboard.append([InlineKeyboardButton("🗑️ Delete All Done Reminders", callback_data="clear_done")])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="panel_back")])
    return InlineKeyboardMarkup(keyboard)


def build_panel_text(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    panel_state = get_panel_state(context)
    mode = panel_state.get("mode")
    if mode == "notes":
        return build_note_panel_text(user_id, int(panel_state.get("page", 0)))
    return build_reminder_panel_text(user_id, bool(panel_state.get("show_done", False)))


def build_panel_keyboard(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> InlineKeyboardMarkup:
    panel_state = get_panel_state(context)
    mode = panel_state.get("mode")
    if mode == "notes":
        return build_note_panel_keyboard(user_id, int(panel_state.get("page", 0)))
    return build_reminder_panel_keyboard(user_id)


async def render_panel_message(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
) -> None:
    panel_state = get_panel_state(context)
    panel_chat_id = panel_state.get("chat_id") or chat_id
    panel_message_id = panel_state.get("message_id")
    text = build_panel_text(context, user_id)
    keyboard = build_panel_keyboard(context, user_id)

    if panel_message_id and panel_chat_id:
        try:
            await context.bot.edit_message_text(
                chat_id=panel_chat_id,
                message_id=panel_message_id,
                text=text,
                reply_markup=keyboard,
            )
            return
        except Exception as error:
            error_text = str(error).lower()
            if "message is not modified" in error_text:
                return

    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
    )
    panel_state["message_id"] = sent.message_id
    panel_state["chat_id"] = chat_id


async def close_panel_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    panel_state = get_panel_state(context)
    panel_chat_id = panel_state.get("chat_id")
    panel_message_id = panel_state.get("message_id")
    if panel_chat_id and panel_message_id:
        try:
            await context.bot.delete_message(chat_id=panel_chat_id, message_id=panel_message_id)
        except Exception:
            logger.debug("Could not delete panel message chat=%s id=%s", panel_chat_id, panel_message_id)
    panel_state["mode"] = None
    panel_state["page"] = 0
    panel_state["show_done"] = False
    panel_state["message_id"] = None
    panel_state["chat_id"] = None


async def open_notes_panel(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    page: int = 0,
) -> None:
    panel_state = get_panel_state(context)
    panel_state["mode"] = "notes"
    panel_state["page"] = max(page, 0)
    panel_state["show_done"] = False
    panel_state["chat_id"] = chat_id
    await render_panel_message(context, user_id, chat_id)


async def open_reminders_panel(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
) -> None:
    panel_state = get_panel_state(context)
    panel_state["mode"] = "reminders"
    panel_state["page"] = 0
    panel_state["show_done"] = False
    panel_state["chat_id"] = chat_id
    await render_panel_message(context, user_id, chat_id)


def get_overview_page_data(user_id: int, mode: str, page: int) -> tuple[str, bool, bool]:
    safe_mode = mode if mode in {"all", "upcoming", "notes", "important"} else "all"
    safe_page = max(page, 0)
    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")
    in_7_days = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    if safe_mode == "all":
        overdue = get_overdue_tasks(user_id)[:5]
        due_today = get_active_tasks_for_due_date(user_id, today_str)[:5]
        upcoming = get_tasks_due_range(user_id, (today + timedelta(days=1)).strftime("%Y-%m-%d"), in_7_days)[:8]

        lines = ["📌 Overview"]
        lines.append("\n⚠️ Overdue")
        lines.extend(
            [f"- #{task['id']} {shorten_text(task['description'], 44)} ({task['due_date']})" for task in overdue]
            if overdue
            else ["- None"]
        )

        lines.append("\n📅 Due Today")
        lines.extend(
            [f"- #{task['id']} {shorten_text(task['description'], 44)}" for task in due_today]
            if due_today
            else ["- None"]
        )

        lines.append("\n⏰ Next 7 Days")
        lines.extend(
            [f"- #{task['id']} {shorten_text(task['description'], 40)} ({task['due_date']})" for task in upcoming]
            if upcoming
            else ["- None"]
        )
        return "\n".join(lines), False, False

    if safe_mode == "upcoming":
        upcoming = get_tasks_due_range(user_id, today_str, in_7_days)
        page_size = 8
        start = safe_page * page_size
        page_items = upcoming[start : start + page_size]
        has_prev = safe_page > 0
        has_next = len(upcoming) > start + page_size
        lines = [f"⏰ Upcoming (7 days) · Page {safe_page + 1}"]
        if not page_items:
            lines.append("No active tasks in the next 7 days.")
        else:
            lines.extend(
                [f"- #{task['id']} {shorten_text(task['description'], 42)} ({task['due_date']})" for task in page_items]
            )
        return "\n".join(lines), has_prev, has_next

    page_size = 5
    offset = safe_page * page_size
    if safe_mode == "important":
        notes = get_important_notes(user_id, limit=page_size + 1, offset=offset)
        title = "⭐ Important Notes"
    else:
        notes = get_recent_notes(user_id, limit=page_size + 1, offset=offset)
        title = "📝 Notes"

    has_prev = safe_page > 0
    has_next = len(notes) > page_size
    notes_page = notes[:page_size]
    lines = [f"{title} · Page {safe_page + 1}"]
    if not notes_page:
        lines.append("No notes found.")
    else:
        for note in notes_page:
            prefix = "⭐ " if note["is_important"] else ""
            lines.append(f"- #{note['id']} {prefix}{shorten_text(note['content'], 56)}")
    return "\n".join(lines), has_prev, has_next


def build_overview_text(user_id: int, mode: str, page: int) -> str:
    text, _, _ = get_overview_page_data(user_id, mode, page)
    return text


def build_overview_keyboard(
    mode: str,
    page: int,
    has_next: bool,
    has_prev: bool,
    note_ids: Optional[list[int]] = None,
) -> InlineKeyboardMarkup:
    safe_mode = mode if mode in {"all", "upcoming", "notes", "important"} else "all"
    safe_page = max(page, 0)
    keyboard: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("📌 All", callback_data="overview:all:0"),
            InlineKeyboardButton("⏰ Upcoming", callback_data="overview:upcoming:0"),
        ],
        [
            InlineKeyboardButton("📝 Notes", callback_data="overview:notes:0"),
            InlineKeyboardButton("⭐ Important", callback_data="overview:important:0"),
        ],
    ]

    nav_row: list[InlineKeyboardButton] = []
    if has_prev:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"overview:{safe_mode}:{safe_page - 1}"))
    if has_next:
        nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"overview:{safe_mode}:{safe_page + 1}"))
    if nav_row:
        keyboard.append(nav_row)

    if note_ids:
        for note_id in note_ids:
            keyboard.append(
                [
                    InlineKeyboardButton(f"🗑️ Delete #{note_id}", callback_data=f"note_delete:{note_id}"),
                    InlineKeyboardButton("⭐ Toggle", callback_data=f"note_star:{note_id}"),
                ]
            )

    keyboard.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="overview_back")])
    return InlineKeyboardMarkup(keyboard)


def normalize_pending_content(parsed: dict, fallback_text: str) -> str:
    value = parsed.get("content") or parsed.get("title") or fallback_text
    return str(value).strip()


def build_pending_confirmation_text(pending: dict) -> str:
    intent_label_map = {
        "CREATE_TASK": "Task",
        "CREATE_NOTE": "Note",
        "SAVE_IMPORTANT": "Important Note",
    }
    intent_label = intent_label_map.get(pending.get("intent"), "Unknown")
    title = pending.get("title") or ""
    content = pending.get("content") or ""
    due_date = pending.get("due_date") or "-"
    due_time = pending.get("due_time") or "-"
    event_at = pending.get("event_at") or "-"
    confidence = pending.get("confidence") or "low"
    return (
        "🧾 Confirm Action\n"
        f"Type: {intent_label}\n"
        f"Title: {title or '-'}\n"
        f"Content: {content or '-'}\n"
        f"Due Date: {due_date}\n"
        f"Due Time: {due_time}\n"
        f"Event At: {event_at}\n"
        f"Confidence: {confidence}"
    )


def build_pending_confirmation_keyboard(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Confirm", callback_data=f"pa_cf:{pending_id}")],
            [InlineKeyboardButton("✏️ Edit", callback_data=f"pa_ed:{pending_id}")],
            [InlineKeyboardButton("🔁 Change Type", callback_data=f"pa_ty:{pending_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"pa_ca:{pending_id}")],
        ]
    )


def build_cancel_edit_keyboard(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel Edit", callback_data=f"pa_xe:{pending_id}")]]
    )


def build_main_text(user_id: int) -> str:
    notes = get_recent_notes(user_id, limit=10)
    reminders = get_active_reminders_flat(user_id)

    lines = ["Note:"]
    if not notes:
        lines.append("- None")
    else:
        for idx, note in enumerate(notes, start=1):
            lines.append(format_note_line(idx, note))

    lines.append("\nReminder:")
    if not reminders:
        lines.append("- None")
    else:
        for idx, reminder in enumerate(reminders, start=1):
            lines.append(format_reminder_line(idx, reminder))

    return "\n".join(lines)


def build_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    for idx, task in enumerate(get_active_reminders_flat(user_id), start=1):
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"☐ Tick {idx}",
                    callback_data=f"done:{task['id']}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton("Edit Note", callback_data="panel_notes"),
            InlineKeyboardButton("Edit Reminder", callback_data="panel_reminders"),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


async def reseat_main_dashboard(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
) -> None:
    old_ref = get_main_message_ref(user_id)
    if old_ref:
        try:
            await context.bot.delete_message(chat_id=old_ref["chat_id"], message_id=old_ref["message_id"])
            logger.info("Deleted old dashboard before reseat for user %s", user_id)
        except Exception as error:
            logger.debug("Could not delete old dashboard for reseat user %s: %s", user_id, error)

    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=build_main_text(user_id),
        reply_markup=build_main_keyboard(user_id),
    )
    save_main_message_ref(user_id, chat_id, sent.message_id)
    logger.info("Reseated dashboard for user %s", user_id)


def should_reseat_dashboard(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    produced_temp_messages: int,
    cooldown_seconds: int = 60,
) -> bool:
    if is_in_edit_panel(context):
        return False

    auto_reseat = get_user_auto_reseat(user_id)
    if produced_temp_messages < 1 and not auto_reseat:
        return False

    now_ts = datetime.now().timestamp()
    last_ts = float(context.user_data.get("last_reseat_at_ts", 0.0))
    if now_ts - last_ts < cooldown_seconds:
        logger.info("Skipping reseat due to cooldown for user %s", user_id)
        return False

    context.user_data["last_reseat_at_ts"] = now_ts
    return True


async def post_write_dashboard_update(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    produced_temp_messages: int,
) -> None:
    if is_in_edit_panel(context):
        await render_panel_message(context, user_id, chat_id)
        return

    await refresh_main_message(context, user_id, chat_id)
    if should_reseat_dashboard(context, user_id, produced_temp_messages):
        await reseat_main_dashboard(context, user_id, chat_id)


async def refresh_main_message(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: Optional[int] = None,
) -> None:
    if is_in_edit_panel(context):
        return

    message_ref = get_main_message_ref(user_id)
    target_chat_id: Optional[int] = chat_id
    if target_chat_id is None and message_ref is not None:
        target_chat_id = message_ref["chat_id"]

    if target_chat_id is not None:
        await reseat_main_dashboard(context, user_id, target_chat_id)
        return

    if message_ref is None:
        logger.info("No main message saved for user %s; recreating=%s", user_id, bool(chat_id))
        if chat_id is None:
            return
        try:
            sent = await context.bot.send_message(
                chat_id=chat_id,
                text=build_main_text(user_id),
                reply_markup=build_main_keyboard(user_id),
            )
            save_main_message_ref(user_id, chat_id, sent.message_id)
            logger.info("Recreated missing main message for user %s", user_id)
        except Exception:
            logger.exception("Failed to recreate missing main message for user %s", user_id)
        return

    try:
        logger.info("Refreshing main message for user %s", user_id)
        await context.bot.edit_message_text(
            chat_id=message_ref["chat_id"],
            message_id=message_ref["message_id"],
            text=build_main_text(user_id),
            reply_markup=build_main_keyboard(user_id),
        )
    except Exception as error:
        error_text = str(error).lower()
        if "message is not modified" in error_text:
            logger.info("Main message unchanged for user %s", user_id)
            return
        # If editing fails for any reason, try to send a new message
        logger.info("Main message edit failed, sending new message for user %s: %s", user_id, error)
        try:
            sent = await context.bot.send_message(
                chat_id=message_ref["chat_id"],
                text=build_main_text(user_id),
                reply_markup=build_main_keyboard(user_id),
            )
            save_main_message_ref(user_id, message_ref["chat_id"], sent.message_id)
            return
        except Exception:
            logger.exception("Failed to recreate main message for user %s", user_id)
            return


async def send_main_message(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
) -> None:
    """Send or update the main task message. Ensures only one exists."""
    logger.info("Sending main message for user %s", user_id)
    
    # Delete previous main message if exists (ensures only one)
    old_ref = get_main_message_ref(user_id)
    if old_ref:
        try:
            await context.bot.delete_message(chat_id=old_ref["chat_id"], message_id=old_ref["message_id"])
            logger.info("Deleted previous main message for user %s", user_id)
        except Exception as e:
            logger.debug("Could not delete previous main message: %s", e)
    
    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=build_main_text(user_id),
        reply_markup=build_main_keyboard(user_id),
    )
    save_main_message_ref(user_id, chat_id, sent.message_id)
    # ✅ DO NOT track this message – it's managed separately


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    set_user_setting_defaults_if_missing(user_id)

    context.user_data.pop("create_step", None)
    context.user_data.pop("new_task_description", None)

    logger.info("/start from user %s", user_id)
    await send_main_message(context, user_id, chat_id)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    msg = await update.message.reply_text(
        "📌 *Available Commands*\n"
        "/start - Open the main task list (with buttons)\n"
        "/newstart - Send a fresh main task message\n"
        "/clear - Delete all messages and reset\n"
        "/list - Show active tasks as text\n"
        "/today - Show active tasks due today\n"
        "/tomorrow - Show active tasks due tomorrow\n"
        "/note - Save your next message as a note\n"
        "/notes - Show your 10 most recent notes\n"
        "/help - Show this message\n"
        "/cancel - Cancel task or note flow",
        parse_mode="Markdown",
    )
    track_message(context, user_id, msg.message_id)


async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Delete temporary messages
    deleted_temp = await clear_temporary_messages(context, user_id, chat_id)

    # Delete main message from DB and from chat
    old_ref = get_main_message_ref(user_id)
    deleted_main = 0
    if old_ref:
        try:
            await context.bot.delete_message(chat_id=old_ref["chat_id"], message_id=old_ref["message_id"])
            with get_db() as conn:
                conn.execute("DELETE FROM user_main_message WHERE user_id = ?", (user_id,))
            deleted_main = 1
            logger.info("Deleted main message for user %s", user_id)
        except Exception as e:
            logger.debug("Could not delete main message: %s", e)

    # Clear conversation state
    context.user_data.pop("create_step", None)
    context.user_data.pop("new_task_description", None)

    # Send confirmation (tracked)
    confirm = await update.message.reply_text(
        f"✅ Cleared {deleted_temp + deleted_main} message(s). Use /start to see your tasks again."
    )
    track_message(context, user_id, confirm.message_id)


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    tasks = get_active_tasks(user_id)
    if not tasks:
        msg = await update.message.reply_text("No active tasks.")
        track_message(context, user_id, msg.message_id)
        await refresh_main_message(context, user_id, chat_id)
        return
    msg = await update.message.reply_text(
        "📋 *Active Tasks*\n" + format_task_list(tasks, "⬜"),
        parse_mode="Markdown",
    )
    track_message(context, user_id, msg.message_id)
    await refresh_main_message(context, user_id, chat_id)
    logger.info("/list sent to user %s with %s tasks", user_id, len(tasks))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    previous_step = context.user_data.pop("create_step", None)
    context.user_data.pop("new_task_description", None)
    context.user_data.pop("pending_edit_id", None)
    context.user_data.pop("note_edit_id", None)
    context.user_data.pop("pending_reminder_draft", None)
    if previous_step == "awaiting_note":
        text = "Note capture cancelled."
    elif previous_step in {"awaiting_description", "awaiting_due_date"}:
        text = "Task creation cancelled."
    elif previous_step == "awaiting_pending_edit_text":
        text = "Edit cancelled."
    elif previous_step == "awaiting_note_edit_text":
        text = "Note edit cancelled."
    elif previous_step == "awaiting_reminder_date":
        text = "Reminder date capture cancelled."
    else:
        text = "Nothing to cancel."
    msg = await update.message.reply_text(text)
    track_message(context, user_id, msg.message_id)
    await refresh_main_message(context, user_id, chat_id)
    logger.info("/cancel by user %s for step %s", user_id, previous_step)


async def newstart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    set_user_setting_defaults_if_missing(user_id)

    context.user_data.pop("create_step", None)
    context.user_data.pop("new_task_description", None)

    logger.info("/newstart from user %s", user_id)
    await send_main_message(context, user_id, chat_id)


async def show_done_tasks(context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int) -> None:
    """Send a message listing all completed tasks."""
    tasks = get_done_tasks(user_id)
    if not tasks:
        text = "No completed tasks."
    else:
        text = "✅ *Completed Tasks*\n" + format_task_list(tasks, "✅")
    msg = await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
    track_message(context, user_id, msg.message_id)


async def show_recent_notes(context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int) -> None:
    notes = get_recent_notes(user_id, limit=10)
    text = format_recent_notes_text(notes)
    keyboard = build_recent_notes_keyboard(notes)
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
    track_message(context, user_id, msg.message_id)
    logger.info("Displayed %s recent notes for user %s", len(notes), user_id)


async def show_important_notes(context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int) -> None:
    notes = get_important_notes(user_id, limit=10)
    if not notes:
        text = "⭐ Important Notes\nNo important notes yet."
        keyboard = None
    else:
        text_lines = ["⭐ Important Notes"]
        for note in notes:
            event_suffix = f" (event: {note['event_at']})" if note["event_at"] else ""
            text_lines.append(f"#{note['id']} ⭐ - {note['content']}{event_suffix}")
        text = "\n".join(text_lines)
        keyboard = build_recent_notes_keyboard(notes)
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
    track_message(context, user_id, msg.message_id)
    logger.info("Displayed %s important notes for user %s", len(notes), user_id)


async def send_overview_message(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    mode: str,
    page: int,
) -> None:
    text, has_prev, has_next = get_overview_page_data(user_id, mode, page)
    note_ids: list[int] = []
    page_size = 5
    offset = max(page, 0) * page_size
    if mode == "notes":
        note_ids = [int(note["id"]) for note in get_recent_notes(user_id, limit=page_size, offset=offset)]
    elif mode == "important":
        note_ids = [int(note["id"]) for note in get_important_notes(user_id, limit=page_size, offset=offset)]
    keyboard = build_overview_keyboard(mode, page, has_next, has_prev, note_ids=note_ids)
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
    track_message(context, user_id, msg.message_id)
    logger.info("Overview shown for user %s mode=%s page=%s", user_id, mode, page)


async def show_pending_confirmation_card(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    pending_id: str,
) -> None:
    store = get_pending_actions_store(context)
    pending = store.get(pending_id)
    if not pending:
        msg = await context.bot.send_message(chat_id=chat_id, text="Pending action expired.")
        track_message(context, user_id, msg.message_id)
        await refresh_main_message(context, user_id, chat_id)
        return

    text = build_pending_confirmation_text(pending)
    keyboard = build_pending_confirmation_keyboard(pending_id)
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
    track_message(context, user_id, msg.message_id)
    await refresh_main_message(context, user_id, chat_id)


def cycle_pending_type(intent: str) -> str:
    if intent == "CREATE_TASK":
        return "CREATE_NOTE"
    if intent == "CREATE_NOTE":
        return "SAVE_IMPORTANT"
    return "CREATE_TASK"


async def run_listing_intent(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    intent: str,
    search_query: str = "",
) -> bool:
    if intent == "LIST_ALL_TASKS":
        tasks = get_active_tasks(user_id)
        if tasks:
            text = "📋 Active Tasks\n" + format_task_list(tasks, "⬜")
        else:
            text = "No active tasks."
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
        track_message(context, user_id, msg.message_id)
        await refresh_main_message(context, user_id, chat_id)
        return True

    if intent == "LIST_TODAY":
        tasks = get_active_tasks_for_due_date(user_id, datetime.now().strftime("%Y-%m-%d"))
        if tasks:
            text = "📅 Active tasks due today\n" + format_task_list(tasks, "⬜")
        else:
            text = "📅 No active tasks due today."
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
        track_message(context, user_id, msg.message_id)
        await refresh_main_message(context, user_id, chat_id)
        return True

    if intent == "LIST_TOMORROW":
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        tasks = get_active_tasks_for_due_date(user_id, tomorrow)
        if tasks:
            text = "🗓️ Active tasks due tomorrow\n" + format_task_list(tasks, "⬜")
        else:
            text = "🗓️ No active tasks due tomorrow."
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
        track_message(context, user_id, msg.message_id)
        await refresh_main_message(context, user_id, chat_id)
        return True

    if intent in {"LIST_WEEK", "UPCOMING_REMINDERS"}:
        start = datetime.now().strftime("%Y-%m-%d")
        end = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        tasks = get_tasks_due_range(user_id, start, end)
        if tasks:
            text = "📆 Active tasks due this week\n" + format_task_list(tasks, "⬜")
        else:
            text = "📆 No active tasks due in the next 7 days."
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
        track_message(context, user_id, msg.message_id)
        await refresh_main_message(context, user_id, chat_id)
        return True

    if intent == "SHOW_NOTES":
        await show_recent_notes(context, user_id, chat_id)
        await refresh_main_message(context, user_id, chat_id)
        return True

    if intent == "SHOW_IMPORTANT_NOTES":
        await show_important_notes(context, user_id, chat_id)
        await refresh_main_message(context, user_id, chat_id)
        return True

    if intent == "OVERVIEW_ALL":
        await clear_temporary_messages(context, user_id, chat_id)
        await send_overview_message(context, user_id, chat_id, "all", 0)
        await refresh_main_message(context, user_id, chat_id)
        return True

    if intent == "SEARCH":
        response = f"Search is not implemented yet. Query: {search_query}" if search_query else "Search is not implemented yet."
        msg = await context.bot.send_message(chat_id=chat_id, text=response)
        track_message(context, user_id, msg.message_id)
        await refresh_main_message(context, user_id, chat_id)
        return True

    return False


async def start_pending_action_from_ai(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    parsed: dict,
    original_text: str,
) -> None:
    pending_id = create_pending_id()
    pending = {
        "intent": parsed.get("intent", "UNKNOWN"),
        "title": parsed.get("title", ""),
        "content": normalize_pending_content(parsed, original_text),
        "due_date": parsed.get("due_date"),
        "due_time": parsed.get("due_time"),
        "event_at": parsed.get("event_at"),
        "search_query": parsed.get("search_query", ""),
        "confidence": parsed.get("confidence", "low"),
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
    }
    store = get_pending_actions_store(context)
    store[pending_id] = pending
    logger.info("Created pending action %s for user %s intent=%s", pending_id, user_id, pending["intent"])
    await clear_temporary_messages(context, user_id, chat_id)
    await show_pending_confirmation_card(context, user_id, chat_id, pending_id)


async def note_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    set_user_setting_defaults_if_missing(user_id)
    await clear_temporary_messages(context, user_id, chat_id)
    context.user_data["create_step"] = "awaiting_note"
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_note")]]
    msg = await update.message.reply_text(
        "Send your note in one message.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    track_message(context, user_id, msg.message_id)
    logger.info("/note started for user %s", user_id)


async def notes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    set_user_setting_defaults_if_missing(user_id)
    await clear_temporary_messages(context, user_id, chat_id)
    await show_recent_notes(context, user_id, chat_id)
    await refresh_main_message(context, user_id, chat_id)


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    set_user_setting_defaults_if_missing(user_id)
    await clear_temporary_messages(context, user_id, chat_id)
    today_date = datetime.now().strftime("%Y-%m-%d")
    tasks = get_active_tasks_for_due_date(user_id, today_date)
    if tasks:
        text = "📅 Active tasks due today\n" + format_task_list(tasks, "⬜")
    else:
        text = "📅 No active tasks due today."
    msg = await update.message.reply_text(text)
    track_message(context, user_id, msg.message_id)
    await refresh_main_message(context, user_id, chat_id)
    logger.info("/today sent to user %s with %s tasks", user_id, len(tasks))


async def tomorrow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    set_user_setting_defaults_if_missing(user_id)
    await clear_temporary_messages(context, user_id, chat_id)
    tomorrow_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    tasks = get_active_tasks_for_due_date(user_id, tomorrow_date)
    if tasks:
        text = "🗓️ Active tasks due tomorrow\n" + format_task_list(tasks, "⬜")
    else:
        text = "🗓️ No active tasks due tomorrow."
    msg = await update.message.reply_text(text)
    track_message(context, user_id, msg.message_id)
    await refresh_main_message(context, user_id, chat_id)
    logger.info("/tomorrow sent to user %s with %s tasks", user_id, len(tasks))


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    chat_id = query.message.chat_id
    set_user_setting_defaults_if_missing(user_id)

    if data == "create_task":
        await clear_temporary_messages(context, user_id, chat_id)
        context.user_data["create_step"] = "awaiting_description"
        msg = await query.message.reply_text("Send task description:")
        track_message(context, user_id, msg.message_id)
        logger.info("Task creation started from dashboard for user %s", user_id)
        return

    if data == "panel_notes":
        await clear_temporary_messages(context, user_id, chat_id)
        await open_notes_panel(context, user_id, chat_id, page=0)
        return

    if data.startswith("panel_notes_page:"):
        try:
            page = int(data.split(":", 1)[1])
        except ValueError:
            page = 0
        await clear_temporary_messages(context, user_id, chat_id)
        await open_notes_panel(context, user_id, chat_id, page=page)
        return

    if data == "panel_reminders":
        await clear_temporary_messages(context, user_id, chat_id)
        await open_reminders_panel(context, user_id, chat_id)
        return

    if data == "panel_back":
        await clear_temporary_messages(context, user_id, chat_id)
        await close_panel_message(context)
        await refresh_main_message(context, user_id, chat_id)
        return

    if data == "reseat_dashboard":
        await clear_temporary_messages(context, user_id, chat_id)
        await reseat_main_dashboard(context, user_id, chat_id)
        return

    if data == "dash_today":
        await clear_temporary_messages(context, user_id, chat_id)
        await run_listing_intent(context, user_id, chat_id, "LIST_TODAY")
        return

    if data == "dash_tomorrow":
        await clear_temporary_messages(context, user_id, chat_id)
        await run_listing_intent(context, user_id, chat_id, "LIST_TOMORROW")
        return

    if data == "dash_week":
        await clear_temporary_messages(context, user_id, chat_id)
        await run_listing_intent(context, user_id, chat_id, "LIST_WEEK")
        return

    if data == "overview_back":
        await clear_temporary_messages(context, user_id, chat_id)
        await refresh_main_message(context, user_id, chat_id)
        logger.info("Overview back to dashboard for user %s", user_id)
        return

    if data.startswith("overview:"):
        parts = data.split(":")
        if len(parts) != 3:
            return
        mode = parts[1]
        try:
            page = int(parts[2])
        except ValueError:
            page = 0
        await clear_temporary_messages(context, user_id, chat_id)
        await send_overview_message(context, user_id, chat_id, mode, page)
        await refresh_main_message(context, user_id, chat_id)
        return

    if data.startswith("pa_ca:"):
        pending_id = data.split(":", 1)[1]
        store = get_pending_actions_store(context)
        store.pop(pending_id, None)
        context.user_data.pop("pending_edit_id", None)
        if context.user_data.get("create_step") == "awaiting_pending_edit_text":
            context.user_data.pop("create_step", None)
        await clear_temporary_messages(context, user_id, chat_id)
        await refresh_main_message(context, user_id, chat_id)
        logger.info("Pending action %s cancelled for user %s", pending_id, user_id)
        return

    if data.startswith("pa_ty:"):
        pending_id = data.split(":", 1)[1]
        store = get_pending_actions_store(context)
        pending = store.get(pending_id)
        if not pending:
            msg = await query.message.reply_text("Pending action expired.")
            track_message(context, user_id, msg.message_id)
            await refresh_main_message(context, user_id, chat_id)
            return
        pending["intent"] = cycle_pending_type(pending.get("intent", "CREATE_TASK"))
        if pending["intent"] == "CREATE_TASK":
            pending["event_at"] = None
        await clear_temporary_messages(context, user_id, chat_id)
        await show_pending_confirmation_card(context, user_id, chat_id, pending_id)
        logger.info("Pending action %s type changed for user %s", pending_id, user_id)
        return

    if data.startswith("pa_ed:"):
        pending_id = data.split(":", 1)[1]
        store = get_pending_actions_store(context)
        if pending_id not in store:
            msg = await query.message.reply_text("Pending action expired.")
            track_message(context, user_id, msg.message_id)
            await refresh_main_message(context, user_id, chat_id)
            return
        context.user_data["create_step"] = "awaiting_pending_edit_text"
        context.user_data["pending_edit_id"] = pending_id
        prompt = await query.message.reply_text(
            "Send the corrected text.",
            reply_markup=build_cancel_edit_keyboard(pending_id),
        )
        track_message(context, user_id, prompt.message_id)
        await refresh_main_message(context, user_id, chat_id)
        logger.info("Pending action %s edit started for user %s", pending_id, user_id)
        return

    if data.startswith("pa_xe:"):
        pending_id = data.split(":", 1)[1]
        if context.user_data.get("pending_edit_id") == pending_id:
            context.user_data.pop("pending_edit_id", None)
            if context.user_data.get("create_step") == "awaiting_pending_edit_text":
                context.user_data.pop("create_step", None)
        await clear_temporary_messages(context, user_id, chat_id)
        msg = await query.message.reply_text("Edit cancelled.")
        track_message(context, user_id, msg.message_id)
        await show_pending_confirmation_card(context, user_id, chat_id, pending_id)
        logger.info("Pending action %s edit cancelled for user %s", pending_id, user_id)
        return

    if data.startswith("pa_cf:"):
        pending_id = data.split(":", 1)[1]
        store = get_pending_actions_store(context)
        pending = store.get(pending_id)
        if not pending:
            msg = await query.message.reply_text("Pending action expired.")
            track_message(context, user_id, msg.message_id)
            await refresh_main_message(context, user_id, chat_id)
            return

        intent = pending.get("intent", "UNKNOWN")
        content = (pending.get("content") or pending.get("title") or "").strip()
        due_date = pending.get("due_date")
        due_time = pending.get("due_time")
        event_at = pending.get("event_at")

        if intent == "CREATE_TASK":
            if not content:
                msg = await query.message.reply_text("Cannot save an empty task.")
                track_message(context, user_id, msg.message_id)
                await refresh_main_message(context, user_id, chat_id)
                return
            add_task(user_id, chat_id, content, due_date, due_time=due_time)
        elif intent == "CREATE_NOTE":
            if not content:
                msg = await query.message.reply_text("Cannot save an empty note.")
                track_message(context, user_id, msg.message_id)
                await refresh_main_message(context, user_id, chat_id)
                return
            add_note(user_id, content, event_at=event_at, is_important=False)
        elif intent == "SAVE_IMPORTANT":
            if not content:
                msg = await query.message.reply_text("Cannot save an empty important note.")
                track_message(context, user_id, msg.message_id)
                await refresh_main_message(context, user_id, chat_id)
                return
            add_note(user_id, content, event_at=event_at, is_important=True)
        else:
            msg = await query.message.reply_text("Unsupported pending action type.")
            track_message(context, user_id, msg.message_id)
            await refresh_main_message(context, user_id, chat_id)
            return

        store.pop(pending_id, None)
        context.user_data.pop("pending_edit_id", None)
        if context.user_data.get("create_step") == "awaiting_pending_edit_text":
            context.user_data.pop("create_step", None)
        deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
        msg = await query.message.reply_text("Saved ✅")
        track_message(context, user_id, msg.message_id)
        await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp + 1)
        logger.info("Pending action %s confirmed for user %s intent=%s", pending_id, user_id, intent)
        return

    if data == "add_note":
        await clear_temporary_messages(context, user_id, chat_id)
        context.user_data["create_step"] = "awaiting_note"
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_note")]]
        msg = await query.message.reply_text(
            "Send your note in one message.",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        track_message(context, user_id, msg.message_id)
        logger.info("Note capture started from dashboard for user %s", user_id)
        return

    if data == "cancel_note":
        await clear_temporary_messages(context, user_id, chat_id)
        context.user_data.pop("create_step", None)
        if is_in_edit_panel(context):
            await render_panel_message(context, user_id, chat_id)
        else:
            msg = await query.message.reply_text("Note capture cancelled.")
            track_message(context, user_id, msg.message_id)
            await refresh_main_message(context, user_id, chat_id)
        logger.info("Note capture cancelled from dashboard for user %s", user_id)
        return

    if data == "show_notes":
        await clear_temporary_messages(context, user_id, chat_id)
        await show_recent_notes(context, user_id, chat_id)
        await refresh_main_message(context, user_id, chat_id)
        return

    if data.startswith("note_delete:"):
        note_id = int(data.split(":", 1)[1])
        deleted = delete_note(user_id, note_id)
        deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
        if is_in_edit_panel(context):
            await render_panel_message(context, user_id, chat_id)
            await query.answer("Deleted ✅" if deleted else "Note not found.", show_alert=False)
        else:
            confirmation = "Deleted ✅" if deleted else "Note not found."
            msg = await query.message.reply_text(confirmation)
            track_message(context, user_id, msg.message_id)
            await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp + 1)
        logger.info("Note delete for user %s note %s result=%s", user_id, note_id, deleted)
        return

    if data.startswith("note_star:"):
        note_id = int(data.split(":", 1)[1])
        changed = toggle_note_important(user_id, note_id)
        deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
        if is_in_edit_panel(context):
            await render_panel_message(context, user_id, chat_id)
            await query.answer("Updated ⭐" if changed else "Note not found.", show_alert=False)
        else:
            confirmation = "Updated ⭐" if changed else "Note not found."
            msg = await query.message.reply_text(confirmation)
            track_message(context, user_id, msg.message_id)
            await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp + 1)
        logger.info("Note star toggle for user %s note %s result=%s", user_id, note_id, changed)
        return

    if data.startswith("note_edit:"):
        note_id = int(data.split(":", 1)[1])
        await clear_temporary_messages(context, user_id, chat_id)
        context.user_data["create_step"] = "awaiting_note_edit_text"
        context.user_data["note_edit_id"] = note_id
        msg = await query.message.reply_text(
            "Send the updated note text.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_note_edit")]]
            ),
        )
        track_message(context, user_id, msg.message_id)
        if is_in_edit_panel(context):
            await render_panel_message(context, user_id, chat_id)
        else:
            await refresh_main_message(context, user_id, chat_id)
        logger.info("Note edit started for user %s note %s", user_id, note_id)
        return

    if data == "cancel_note_edit":
        context.user_data.pop("note_edit_id", None)
        if context.user_data.get("create_step") == "awaiting_note_edit_text":
            context.user_data.pop("create_step", None)
        await clear_temporary_messages(context, user_id, chat_id)
        if is_in_edit_panel(context):
            await render_panel_message(context, user_id, chat_id)
        else:
            msg = await query.message.reply_text("Note edit cancelled.")
            track_message(context, user_id, msg.message_id)
            await refresh_main_message(context, user_id, chat_id)
        return

    if data == "cancel_task":
        # Clear temp and send fresh main message
        await clear_temporary_messages(context, user_id, chat_id)
        context.user_data.pop("create_step", None)
        context.user_data.pop("new_task_description", None)
        await refresh_main_message(context, user_id, chat_id)
        return

    if data == "show_done":
        await clear_temporary_messages(context, user_id, chat_id)
        if is_in_edit_panel(context):
            panel_state = get_panel_state(context)
            panel_state["show_done"] = True
            await render_panel_message(context, user_id, chat_id)
        else:
            await show_done_tasks(context, user_id, chat_id)
            await refresh_main_message(context, user_id, chat_id)
        return

    if data == "clear_done":
        cleared = clear_done_tasks(user_id)
        deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
        if is_in_edit_panel(context):
            await render_panel_message(context, user_id, chat_id)
            await query.answer(f"Cleared {cleared} done reminder(s).", show_alert=False)
        else:
            msg = await query.message.reply_text(f"🧹 Cleared {cleared} done task(s).")
            track_message(context, user_id, msg.message_id)
            await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp + 1)
        return

    if data.startswith("done:"):
        task_id = int(data.split(":", 1)[1])
        changed = mark_task_done(user_id, task_id)
        deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
        if is_in_edit_panel(context):
            await render_panel_message(context, user_id, chat_id)
        else:
            await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp)
        if changed:
            await query.answer("Task marked as done ✅", show_alert=False)
        else:
            await query.answer("Task already done or not found.", show_alert=False)
        return


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    step = context.user_data.get("create_step")
    if not step:
        text = update.message.text.strip()
        if not text:
            return

        normalized = text.lower().strip()
        if normalized in {"edit note", "edit note:"}:
            await clear_temporary_messages(context, user_id, chat_id)
            await open_notes_panel(context, user_id, chat_id, page=0)
            return

        if normalized in {"edit reminder", "edit reminder:"}:
            await clear_temporary_messages(context, user_id, chat_id)
            await open_reminders_panel(context, user_id, chat_id)
            return

        if re.match(r"^\s*note\b", text, flags=re.IGNORECASE):
            note_content = strip_prefix(text, "note")
            if not note_content:
                msg = await update.message.reply_text("Note cannot be empty.")
                track_message(context, user_id, msg.message_id)
                return
            add_note(user_id, note_content)
            deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
            msg = await update.message.reply_text("Note saved 📝")
            track_message(context, user_id, msg.message_id)
            await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp + 1)
            return

        reminder_forced = False
        reminder_content = text
        if re.match(r"^\s*remind\b", text, flags=re.IGNORECASE):
            reminder_forced = True
            reminder_content = strip_prefix(text, "remind") or text

        now_dt = datetime.now()
        if reminder_forced:
            intent = "REMINDER"
            ai_result = {"content": reminder_content}
        else:
            ai_result = classify_note_or_reminder(text, now=now_dt)
            intent = ai_result["intent"]

        if intent == "NOTE":
            note_content = (ai_result.get("content") or ai_result.get("title") or text).strip()
            if not note_content:
                msg = await update.message.reply_text("Note cannot be empty.")
                track_message(context, user_id, msg.message_id)
                return
            add_note(user_id, note_content)
            deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
            msg = await update.message.reply_text("Note saved 📝")
            track_message(context, user_id, msg.message_id)
            await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp + 1)
            return

        reminder_text = (ai_result.get("content") or ai_result.get("title") or reminder_content or text).strip()
        due_date, due_time = parse_local_reminder_datetime(text, now=now_dt)

        if due_time and not due_date:
            context.user_data["pending_reminder_draft"] = {
                "description": reminder_text,
                "due_time": due_time,
            }
            context.user_data["create_step"] = "awaiting_reminder_date"
            msg = await update.message.reply_text(
                "What date is this for? (DD/MM/YYYY or DD/MM)\nType 'no' to cancel."
            )
            track_message(context, user_id, msg.message_id)
            return

        add_task(user_id, chat_id, reminder_text, due_date, due_time=due_time)
        deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
        msg = await update.message.reply_text("Reminder saved ✅")
        track_message(context, user_id, msg.message_id)
        await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp + 1)
        return

    text = update.message.text.strip()

    if step == "awaiting_reminder_date":
        normalized = text.lower().strip()
        draft = context.user_data.get("pending_reminder_draft")
        if not draft:
            context.user_data.pop("create_step", None)
            msg = await update.message.reply_text("Reminder draft expired.")
            track_message(context, user_id, msg.message_id)
            await refresh_main_message(context, user_id, chat_id)
            return

        if normalized in {"no", "n"}:
            context.user_data.pop("pending_reminder_draft", None)
            context.user_data.pop("create_step", None)
            msg = await update.message.reply_text("Reminder cancelled.")
            track_message(context, user_id, msg.message_id)
            await refresh_main_message(context, user_id, chat_id)
            return

        if normalized in {"yes", "y"}:
            msg = await update.message.reply_text("Please enter a date (DD/MM/YYYY or DD/MM).")
            track_message(context, user_id, msg.message_id)
            return

        due_date = parse_date_from_text(text, now=datetime.now())
        if not due_date:
            msg = await update.message.reply_text(
                "Invalid date. Please send date as DD/MM/YYYY or DD/MM, or type 'no' to cancel."
            )
            track_message(context, user_id, msg.message_id)
            return

        add_task(
            user_id,
            chat_id,
            draft["description"],
            due_date,
            due_time=draft.get("due_time"),
        )
        context.user_data.pop("pending_reminder_draft", None)
        context.user_data.pop("create_step", None)
        deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
        msg = await update.message.reply_text("Reminder saved ✅")
        track_message(context, user_id, msg.message_id)
        await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp + 1)
        return

    if step == "awaiting_pending_edit_text":
        pending_id = context.user_data.get("pending_edit_id")
        store = get_pending_actions_store(context)
        pending = store.get(pending_id) if pending_id else None
        if not pending:
            context.user_data.pop("pending_edit_id", None)
            context.user_data.pop("create_step", None)
            msg = await update.message.reply_text("Pending action expired.")
            track_message(context, user_id, msg.message_id)
            await refresh_main_message(context, user_id, chat_id)
            return

        parsed = ai_parse_message(text, datetime.now())
        pending["title"] = parsed.get("title", "")
        pending["content"] = normalize_pending_content(parsed, text)
        pending["due_date"] = parsed.get("due_date")
        pending["due_time"] = parsed.get("due_time")
        pending["event_at"] = parsed.get("event_at")
        pending["search_query"] = parsed.get("search_query", "")
        pending["confidence"] = parsed.get("confidence", "low")
        if parsed.get("intent") in CREATE_INTENTS:
            pending["intent"] = parsed["intent"]

        context.user_data.pop("pending_edit_id", None)
        context.user_data.pop("create_step", None)
        await clear_temporary_messages(context, user_id, chat_id)
        await show_pending_confirmation_card(context, user_id, chat_id, pending_id)
        logger.info("Pending action %s updated from edit text for user %s", pending_id, user_id)
        return

    if step == "awaiting_note_edit_text":
        note_id = context.user_data.get("note_edit_id")
        if not note_id:
            context.user_data.pop("create_step", None)
            msg = await update.message.reply_text("Note edit expired.")
            track_message(context, user_id, msg.message_id)
            await refresh_main_message(context, user_id, chat_id)
            return

        if not text:
            msg = await update.message.reply_text("Note text cannot be empty. Send updated text.")
            track_message(context, user_id, msg.message_id)
            return

        changed = update_note_content(user_id, int(note_id), text)
        context.user_data.pop("note_edit_id", None)
        context.user_data.pop("create_step", None)
        deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
        if is_in_edit_panel(context):
            await render_panel_message(context, user_id, chat_id)
        else:
            msg = await update.message.reply_text("Updated ✅" if changed else "Note not found.")
            track_message(context, user_id, msg.message_id)
            await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp + 1)
        logger.info("Note edit saved for user %s note %s result=%s", user_id, note_id, changed)
        return

    if step == "awaiting_note":
        if not text:
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_note")]]
            msg = await update.message.reply_text(
                "Note cannot be empty. Send your note in one message.",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            track_message(context, user_id, msg.message_id)
            return

        add_note(user_id, text)
        context.user_data.pop("create_step", None)
        deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
        if is_in_edit_panel(context):
            await render_panel_message(context, user_id, chat_id)
        else:
            msg = await update.message.reply_text("Note saved 📝")
            track_message(context, user_id, msg.message_id)
            await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp + 1)
        logger.info("Saved note for user %s", user_id)
        return

    if step == "awaiting_description":
        if not text:
            msg = await update.message.reply_text("Description cannot be empty. Send task description:")
            track_message(context, user_id, msg.message_id)
            return
        context.user_data["new_task_description"] = text
        context.user_data["create_step"] = "awaiting_due_date"
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_task")]]
        msg = await update.message.reply_text(
            "Due date? Use: 0, 00, DD, DD/MM, DD/MM/YYYY",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        track_message(context, user_id, msg.message_id)
        return

    if step == "awaiting_due_date":
        parsed_due, parse_error = parse_due_date(text)
        if parse_error:
            # Show error with full prompt format again so user can retry
            keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel_task")]]
            msg = await update.message.reply_text(
                f"{parse_error}\nDue date? Use: 0, 00, DD, DD/MM, DD/MM/YYYY",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            track_message(context, user_id, msg.message_id)
            return

        description = context.user_data.pop("new_task_description", None)
        context.user_data.pop("create_step", None)
        if not description:
            msg = await update.message.reply_text("Task creation reset. Press Create task again.")
            track_message(context, user_id, msg.message_id)
            return

        add_task(user_id, chat_id, description, parsed_due)
        # Clear temp → Show confirmation → Show main
        deleted_temp = await clear_temporary_messages(context, user_id, chat_id)
        msg = await update.message.reply_text("Task added ✅")
        track_message(context, user_id, msg.message_id)
        await post_write_dashboard_update(context, user_id, chat_id, produced_temp_messages=deleted_temp + 1)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    msg = await update.message.reply_text(
        "Sorry, I didn't understand that. Use /help to see available commands."
    )
    track_message(context, user_id, msg.message_id)


async def log_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)


async def startup_notify(app: Application) -> None:
    known_users = get_known_users()
    for user in known_users:
        try:
            help_text = (
                "📌 Available Commands\n"
                "/start - Open the main task list (with buttons)\n"
                "/newstart - Send a fresh main task message\n"
                "/clear - Delete all messages and reset\n"
                "/list - Show active tasks as text\n"
                "/today - Show active tasks due today\n"
                "/tomorrow - Show active tasks due tomorrow\n"
                "/note - Save your next message as a note\n"
                "/notes - Show your 10 most recent notes\n"
                "/help - Show this message\n"
                "/cancel - Cancel task or note flow"
            )
            await app.bot.send_message(
                chat_id=user["chat_id"],
                text=f"✅ Bot is online!\n\n{help_text}",
            )
            # Note: startup help message is not tracked (user may want to keep it)
            await send_main_message(app, user["user_id"], user["chat_id"])
        except Exception as error:
            logger.warning("Could not notify user %s: %s", user["user_id"], error)

    if not OWNER_USER_ID:
        logger.warning("OWNER_USER_ID not set, skipping owner startup notification")
        return




def on_shutdown_signal(signum: int, frame: object) -> None:
    logger.info("Received signal %s. Shutting down...", signum)


def build_application() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newstart", newstart))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("tomorrow", tomorrow_cmd))
    app.add_handler(CommandHandler("note", note_cmd))
    app.add_handler(CommandHandler("notes", notes_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    app.add_error_handler(log_error)
    app.post_init = startup_notify
    return app


def main() -> None:
    init_db()
    signal.signal(signal.SIGINT, on_shutdown_signal)
    signal.signal(signal.SIGTERM, on_shutdown_signal)

    app = build_application()
    logger.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
