"""
Celery tasks for the new bread system - baking timers and state management
"""

from celery import Celery
from datetime import datetime
from typing import List
from application import crud
from application.database import SessionLocal
from application.new_bread_system import BakeryQueueSystem, BREAD_NAMES, BreadState
from application.helpers import redis_helper
from application.logger_config import celery_logger
from application.helpers.general_helpers import seconds_until_midnight_iran
import json
import asyncio
from redis import asyncio as aioredis
from application.setting import settings
import functools
import traceback
from uuid import uuid4
import requests

# Create local celery app reference
celery_app = Celery(
    "tasks",
    broker=settings.CELERY_BROKER_URL,
    backend=None
)


def handle_task_errors(func):
    """Decorator to handle task errors consistently"""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except Exception as e:
            retries = getattr(self.request, "retries", None)
            max_retries = getattr(self, "max_retries", None)
            error_id = uuid4().hex
            tb = traceback.format_exc()

            celery_logger.error(
                f"Celery task {func.__name__} failed",
                extra={"error": str(e), "traceback": tb},
            )
            raise
    return wrapper


def report_to_admin_api(msg, message_thread_id=None):
    """Send report to admin via Telegram"""
    try:
        json_data = {'chat_id': settings.TELEGRAM_CHAT_ID, 'text': msg[:4096]}
        if message_thread_id:
            json_data['message_thread_id'] = message_thread_id

        response = requests.post(
            url=f"https://api.telegram.org/bot{settings.TELEGRAM_TOKEN}/sendMessage",
            json=json_data,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        celery_logger.error(f"Failed to send admin report: {e}")
        return None


# --- Bakery Queue System State Management in Redis ---

REDIS_KEY_BREAD_SYSTEM = "bakery:{0}:bread_system"


async def load_bread_system(r, bakery_id: int) -> BakeryQueueSystem:
    """Load bread system state from Redis or create new"""
    key = REDIS_KEY_BREAD_SYSTEM.format(bakery_id)
    raw = await r.get(key)

    if raw:
        try:
            data = json.loads(raw)
            baking_time = await redis_helper.get_baking_time_s(r, bakery_id)
            system = BakeryQueueSystem.from_dict({**data, "baking_time_seconds": baking_time})
            return system
        except Exception as e:
            celery_logger.error(f"Error loading bread system for bakery {bakery_id}: {e}")

    # Create new system with bakery's baking time
    baking_time = await redis_helper.get_baking_time_s(r, bakery_id)
    return BakeryQueueSystem(baking_time_seconds=baking_time)


async def save_bread_system(r, bakery_id: int, system: BakeryQueueSystem) -> None:
    """Save bread system state to Redis"""
    key = REDIS_KEY_BREAD_SYSTEM.format(bakery_id)
    data = system.to_dict()
    ttl = redis_helper.seconds_until_midnight_iran()
    await r.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)


# --- Celery Tasks for New Bread System ---


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def check_baking_breads(self, bakery_id: int):
    """
    Periodic task to check all baking breads and update their state to READY when done.
    This runs every few seconds to check baking progress.
    """
    async def _check():
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            system = await load_bread_system(r, bakery_id)

            # Check and update bread states
            newly_ready = system.check_and_update_bread_states()

            # Save updated state
            if newly_ready:
                await save_bread_system(r, bakery_id, system)

                # Log the ready tickets
                for ticket_num in newly_ready:
                    msg = f"🍞 Ticket #{ticket_num} is READY for delivery!"
                    celery_logger.info(msg, extra={"bakery_id": bakery_id, "ticket_number": ticket_num})

                    # Notify via Telegram
                    report_to_admin_api.delay(
                        f"Bakery ID: {bakery_id}\nTicket Number: {ticket_num}\nStatus: Ready for delivery",
                        settings.BAKERY_TICKET_THREAD_ID
                    )

            return {"newly_ready_tickets": newly_ready}
        finally:
            await r.close()

    return asyncio.run(_check())


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def start_bread_baking_timer(self, bakery_id: int, ticket_number: int, bread_index: int):
    """
    Start a timer for a specific bread to be baked.
    This task schedules the bread to be marked as ready after baking time.
    """
    async def _start_timer():
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            baking_time = await redis_helper.get_baking_time_s(r, bakery_id)

            # Schedule the completion task
            complete_bread_baking.apply_async(
                args=[bakery_id, ticket_number, bread_index],
                countdown=baking_time
            )

            celery_logger.info(
                f"Started baking timer for Ticket #{ticket_number}, bread #{bread_index}",
                extra={"bakery_id": bakery_id, "baking_time": baking_time}
            )

            return {"status": "timer_started", "baking_time": baking_time}
        finally:
            await r.close()

    return asyncio.run(_start_timer())


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def complete_bread_baking(self, bakery_id: int, ticket_number: int, bread_index: int):
    """
    Mark a specific bread as ready after baking time has elapsed.
    """
    async def _complete():
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            system = await load_bread_system(r, bakery_id)

            # Find the ticket and update the bread state
            found = False
            for ticket in system.all_tickets_history:
                if ticket.number == ticket_number and not ticket.is_cancelled:
                    # Find the bread at the given index
                    if 0 <= bread_index < len(ticket.breads):
                        bread = ticket.breads[bread_index]
                        if bread.state == BreadState.BAKING.value:
                            bread.state = BreadState.READY.value
                            bread.ready_time = datetime.now().timestamp()
                            found = True
                            break

            if found:
                await save_bread_system(r, bakery_id, system)

                # Check if entire ticket is now ready
                ticket = next(
                    (t for t in system.all_tickets_history
                     if t.number == ticket_number and not t.is_cancelled and not t.is_delivered),
                    None
                )
                if ticket and ticket.is_fully_baked():
                    # Trigger auto-dispatch if needed
                    from application import tasks
                    tasks.send_ticket_to_wait_list.delay(ticket_number, bakery_id)

                return {"status": "bread_ready", "ticket_number": ticket_number}

            return {"status": "not_found"}
        finally:
            await r.close()

    return asyncio.run(_complete())


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def process_new_ticket(self, bakery_id: int, counts_list: List[int], customer_id: int = None):
    """
    Process a new ticket request using the new bread system.
    """
    async def _process():
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            system = await load_bread_system(r, bakery_id)

            # Request the ticket
            ticket, message = system.request_ticket(counts_list)

            if ticket:
                # Save system state
                await save_bread_system(r, bakery_id, system)

                # Update Redis with new queue state
                await redis_helper.save_queue_state(r, bakery_id, system.to_dict())

                # Log the new ticket
                celery_logger.info(
                    f"New ticket #{ticket.number} created",
                    extra={"bakery_id": bakery_id, "ticket": ticket.to_dict()}
                )

                return {
                    "status": "success",
                    "ticket_number": ticket.number,
                    "ticket_type": ticket.type_name,
                    "message": message,
                }

            return {"status": "error", "message": message}
        finally:
            await r.close()

    return asyncio.run(_process())


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def process_urgent_bread(self, bakery_id: int, ticket_number: int, counts_list: List[int]):
    """
    Process urgent bread injection for an existing ticket.
    """
    async def _process():
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            system = await load_bread_system(r, bakery_id)

            # Request urgent bread
            success, message = system.request_urgent_bread(ticket_number, counts_list)

            if success:
                await save_bread_system(r, bakery_id, system)

                # Log to database
                with SessionLocal() as db:
                    bread_map = {str(i): count for i, count in enumerate(counts_list)}
                    crud.create_urgent_bread_log(
                        db,
                        bakery_id=bakery_id,
                        urgent_id=f"urgent_{ticket_number}_{datetime.now().timestamp()}",
                        ticket_id=ticket_number,
                        status="PENDING",
                        original_breads=bread_map,
                        remaining_breads=bread_map,
                    )

                return {"status": "success", "message": message}

            return {"status": "error", "message": message}
        finally:
            await r.close()

    return asyncio.run(_process())


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def edit_ticket_task(self, bakery_id: int, ticket_number: int, new_counts: List[int]):
    """
    Edit a ticket that is still in queue.
    """
    async def _edit():
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            system = await load_bread_system(r, bakery_id)

            success, message = system.edit_ticket(ticket_number, new_counts)

            if success:
                await save_bread_system(r, bakery_id, system)

                # Update database
                with SessionLocal() as db:
                    bread_map = {str(i): count for i, count in enumerate(new_counts)}
                    crud.update_customer_breads_for_ticket_today(db, bakery_id, ticket_number, bread_map)

                return {"status": "success", "message": message}

            return {"status": "error", "message": message}
        finally:
            await r.close()

    return asyncio.run(_edit())


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def cancel_ticket_task(self, bakery_id: int, ticket_number: int):
    """
    Cancel a ticket and burn its slot.
    """
    async def _cancel():
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            system = await load_bread_system(r, bakery_id)

            success, message = system.cancel_ticket(ticket_number)

            if success:
                await save_bread_system(r, bakery_id, system)

                # Update Redis queue state
                await redis_helper.save_queue_state(r, bakery_id, system.to_dict())

                # Update database
                with SessionLocal() as db:
                    crud.delete_customer_by_ticket_id_today(db, bakery_id, ticket_number)

                return {"status": "success", "message": message}

            return {"status": "error", "message": message}
        finally:
            await r.close()

    return asyncio.run(_cancel())


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def put_bread_in_oven_task(self, bakery_id: int, ticket_number: int = None):
    """
    Put the next waiting bread into the oven and start baking timer.
    """
    async def _put_in_oven():
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            system = await load_bread_system(r, bakery_id)

            # Put bread in oven
            result = system.put_bread_in_oven()

            # Start baking timer for the bread
            if system.current_baker_task:
                ticket = system.current_baker_task
                bread = ticket.get_next_waiting_bread()
                if bread:
                    # Start the timer
                    start_bread_baking_timer.delay(bakery_id, ticket.number, ticket.breads.index(bread))

            await save_bread_system(r, bakery_id, system)

            return {"status": "success", "message": result}
        finally:
            await r.close()

    return asyncio.run(_put_in_oven())


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 5})
@handle_task_errors
def deliver_ticket_task(self, bakery_id: int, ticket_number: int):
    """
    Deliver a ticket and mark all its breads as delivered.
    """
    async def _deliver():
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            system = await load_bread_system(r, bakery_id)

            result = system.deliver_ticket(ticket_number)

            await save_bread_system(r, bakery_id, system)

            # Update database - consume breads
            with SessionLocal() as db:
                crud.consume_breads_for_customer_today(db, bakery_id, ticket_number)

            return {"status": "success", "message": result}
        finally:
            await r.close()

    return asyncio.run(_deliver())


# Update the beat schedule to include the new tasks
celery_app.conf.beat_schedule = {
    **(celery_app.conf.beat_schedule or {}),
    "check_baking_breads": {
        "task": "application.new_bread_system_tasks.check_baking_breads",
        "schedule": 5.0,  # Check every 5 seconds
    },
}


# For backwards compatibility - convert counts dict to list
def convert_bread_requirements_to_counts(bread_requirements: dict, bread_ids_sorted: list) -> list:
    """Convert bread requirements dict to counts list"""
    return [int(bread_requirements.get(str(bid), 0)) for bid in bread_ids_sorted]
