from fastapi import Depends, HTTPException
from application.database import SessionLocal
from application.tasks import report_to_admin_api
from application.setting import settings
import traceback
from uuid import uuid4
from application.logger_config import logger
import json

def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

from functools import wraps


async def report_to_admin(level: str, fun_name: str, msg: str):
    """Generic Telegram reporting helper.

    level: logical level / category (e.g. 'info', 'error', 'ticket', 'rate').
    fun_name: context or function name for the log.
    msg: main message body (multi-line allowed).
    """
    try:
        report_level = {
            "info": {"thread_id": settings.INFO_THREAD_ID, "emoji": "🔵"},
            "warning": {"thread_id": settings.INFO_THREAD_ID, "emoji": "🟡"},
            "error": {"thread_id": settings.ERR_THREAD_ID, "emoji": "🔴"},
            "emergency_error": {"thread_id": settings.ERR_THREAD_ID, "emoji": "🔴🔴"},
            "ticket": {"thread_id": settings.BAKERY_TICKET_THREAD_ID, "emoji": "🎫"},
            "rate": {"thread_id": settings.RATE_THREAD_ID, "emoji": "⭐"},
            "hardware_error": {"thread_id": settings.HARDWARE_CLIENT_ERROR_THREAD_ID, "emoji": "🔴"},
        }

        conf = report_level.get(level, {})
        emoji = conf.get("emoji", "🔵")
        thread_id = conf.get("thread_id", settings.INFO_THREAD_ID)

        message = f"{emoji} Report {level.replace('_', ' ')} {fun_name}\n\n{msg}"

        report_to_admin_api.delay(message, message_thread_id=thread_id)

    except Exception as e:
        logger.error("error in report_to_admin", extra={"error": str(e)})


def format_admin_event_message(event_title: str, fields: dict | None = None, bread_requirements: dict | None = None) -> str:
    """Build a cleaner Telegram-ready event message body.

    This helper keeps event reports consistent and easy to scan.
    """
    lines = [f"📌 {event_title}"]

    if fields:
        for key, value in fields.items():
            label = str(key).replace('_', ' ').strip().title()
            if isinstance(value, (dict, list, tuple, set)):
                pretty_value = json.dumps(value, ensure_ascii=False, indent=2)
                lines.append(f"• {label}:\n<pre>{pretty_value}</pre>")
            else:
                lines.append(f"• {label}: <code>{value}</code>")

    if bread_requirements:
        lines.append("\n🍞 Bread Requirements:")
        for bread_name_or_id, count in bread_requirements.items():
            lines.append(f"  - {bread_name_or_id}: {count}")

    return "\n".join(lines)


async def log_and_report_error(context: str, error: Exception, extra: dict = None):
    tb = traceback.format_exc()
    error_id = uuid4().hex
    extra = extra or {}
    extra["error_id"] = error_id
    logger.error(
        context, extra={"error": str(error), "traceback": tb, **extra}
    )
    err_msg = (
        f"Error type: {type(error)}"
        f"\nError reason: {str(error)}"
        f"\n\nExtra Info:"
        f"\n{extra}"
        f"\n\nTraceback:\n{tb}"
    )

    await report_to_admin("error", context, err_msg)

def db_transaction(context: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, db, **kwargs):
            try:
                return await func(*args, db=db, **kwargs)
            except HTTPException as e:
                raise e
            except Exception as e:
                db.rollback()
                await log_and_report_error(f"{context}:{func.__name__}", e, extra={})
                raise HTTPException(status_code=500, detail={
                    "message": "Internal server error",
                    "type": type(e).__name__,
                    "reason": str(e)
                })
        return wrapper
    return decorator

def handle_endpoint_errors(context: str):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except HTTPException as e:
                raise e
            except Exception as e:
                await log_and_report_error(f"{context}:{func.__name__}", e, extra={})
                raise HTTPException(status_code=500, detail={
                    "message": "Internal server error",
                    "type": type(e).__name__,
                    "reason": str(e)
                })
        return wrapper
    return decorator
