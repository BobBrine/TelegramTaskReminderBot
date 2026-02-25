import logging
import os
import re
import signal
import sqlite3
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


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with get_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                description TEXT NOT NULL,
                due_date TEXT,
                status TEXT NOT NULL DEFAULT 'active',
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
    task_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if "due_date" not in task_columns:
        connection.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT")
    if "due_at" in task_columns:
        connection.execute(
            """
            UPDATE tasks
            SET due_date = substr(due_at, 1, 10)
            WHERE (due_date IS NULL OR due_date = '') AND due_at IS NOT NULL
            """
        )


def add_task(
    user_id: int,
    chat_id: int,
    description: str,
    due_date: Optional[str],
) -> int:
    with get_db() as connection:
        task_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
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

        cursor = connection.execute(
            f"INSERT INTO tasks ({', '.join(fields)}) VALUES ({', '.join(['?'] * len(fields))})",
            values,
        )
        return int(cursor.lastrowid)


def get_active_tasks(user_id: int) -> list[sqlite3.Row]:
    with get_db() as connection:
        return connection.execute(
            """
            SELECT id,
                   description,
                   COALESCE(due_date, substr(due_at, 1, 10)) AS due_date
            FROM tasks
            WHERE user_id = ? AND status = 'active'
            ORDER BY id ASC
            """,
            (user_id,),
        ).fetchall()


def get_done_tasks(user_id: int) -> list[sqlite3.Row]:
    with get_db() as connection:
        return connection.execute(
            """
            SELECT id,
                   description,
                   COALESCE(due_date, substr(due_at, 1, 10)) AS due_date
            FROM tasks
            WHERE user_id = ? AND status = 'done'
            ORDER BY id DESC
            """,
            (user_id,),
        ).fetchall()


def mark_task_done(user_id: int, task_id: int) -> bool:
    with get_db() as connection:
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


def build_main_text(user_id: int) -> str:
    return "📋 *Your Active Tasks*"


def build_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    for task in get_active_tasks(user_id):
        due_label = due_with_day_label(task["due_date"])
        short_text = f"{task['description']} | {due_label}"
        if len(short_text) > 60:
            short_text = short_text[:57] + "..."
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"☐ {short_text}",
                    callback_data=f"done:{task['id']}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton("🗑️ Clear all done tasks", callback_data="clear_done"),
            InlineKeyboardButton("📋 Show done tasks", callback_data="show_done"),
        ]
    )
    keyboard.append([InlineKeyboardButton("➕ Create task", callback_data="create_task")])
    return InlineKeyboardMarkup(keyboard)


async def refresh_main_message(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    message_ref = get_main_message_ref(user_id)
    if message_ref is None:
        logger.info("No main message saved for user %s", user_id)
        return

    try:
        logger.info("Refreshing main message for user %s", user_id)
        await context.bot.edit_message_text(
            chat_id=message_ref["chat_id"],
            message_id=message_ref["message_id"],
            text=build_main_text(user_id),
            parse_mode="Markdown",
            reply_markup=build_main_keyboard(user_id),
        )
    except Exception as error:
        error_text = str(error).lower()
        if "message is not modified" in error_text:
            logger.info("Main message unchanged for user %s", user_id)
            return
        # If editing fails for any reason, try to send a new message
        logger.info("Main message edit failed, sending new message for user %s", user_id)
        try:
            sent = await context.bot.send_message(
                chat_id=message_ref["chat_id"],
                text=build_main_text(user_id),
                parse_mode="Markdown",
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
        parse_mode="Markdown",
        reply_markup=build_main_keyboard(user_id),
    )
    save_main_message_ref(user_id, chat_id, sent.message_id)
    # ✅ DO NOT track this message – it's managed separately


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

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
        "/help - Show this message\n"
        "/cancel - Cancel task creation",
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
    tasks = get_active_tasks(user_id)
    if not tasks:
        msg = await update.message.reply_text("No active tasks.")
        track_message(context, user_id, msg.message_id)
        return
    msg = await update.message.reply_text(
        "📋 *Active Tasks*\n" + format_task_list(tasks, "⬜"),
        parse_mode="Markdown",
    )
    track_message(context, user_id, msg.message_id)
    logger.info("/list sent to user %s with %s tasks", user_id, len(tasks))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    context.user_data.pop("create_step", None)
    context.user_data.pop("new_task_description", None)
    msg = await update.message.reply_text("Task creation cancelled.")
    track_message(context, user_id, msg.message_id)


async def newstart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

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


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    chat_id = query.message.chat_id

    if data == "create_task":
        await clear_temporary_messages(context, user_id, chat_id)
        context.user_data["create_step"] = "awaiting_description"
        msg = await query.message.reply_text("Send task description:")
        track_message(context, user_id, msg.message_id)
        return

    if data == "cancel_task":
        # Clear temp and send fresh main message
        await clear_temporary_messages(context, user_id, chat_id)
        context.user_data.pop("create_step", None)
        context.user_data.pop("new_task_description", None)
        await send_main_message(context, user_id, chat_id)
        return

    if data == "show_done":
        # Clear temp → Show results → Show main
        await clear_temporary_messages(context, user_id, chat_id)
        await show_done_tasks(context, user_id, chat_id)
        await refresh_main_message(context, user_id)
        return

    if data == "clear_done":
        # Clear done tasks → Clear temp → Show result → Show main
        cleared = clear_done_tasks(user_id)
        await clear_temporary_messages(context, user_id, chat_id)
        msg = await query.message.reply_text(f"🧹 Cleared {cleared} done task(s).")
        track_message(context, user_id, msg.message_id)
        await refresh_main_message(context, user_id)
        return

    if data.startswith("done:"):
        # Mark task done → Clear temp → Refresh main
        task_id = int(data.split(":", 1)[1])
        changed = mark_task_done(user_id, task_id)
        await clear_temporary_messages(context, user_id, chat_id)
        await refresh_main_message(context, user_id)
        if changed:
            await query.answer("Task marked as done ✅", show_alert=False)
        else:
            await query.answer("Task already done or not found.", show_alert=False)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    step = context.user_data.get("create_step")
    if not step:
        text = update.message.text.strip()
        if not text:
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

    text = update.message.text.strip()

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

        chat_id = update.effective_chat.id
        add_task(user_id, chat_id, description, parsed_due)
        # Clear temp → Show confirmation → Show main
        await clear_temporary_messages(context, user_id, chat_id)
        msg = await update.message.reply_text("Task added ✅")
        track_message(context, user_id, msg.message_id)
        await send_main_message(context, user_id, chat_id)


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
                "/help - Show this message\n"
                "/cancel - Cancel task creation"
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
