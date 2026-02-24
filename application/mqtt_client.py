import json
import asyncio
import time
from application.tasks import report_to_admin_api
from application.helpers import endpoint_helper
from application.setting import settings
import aiomqtt
from application.logger_config import logger as app_logger

MQTT_BAKERY_PREFIX = "bakery/{0}"
MQTT_UPDATE_BREAD_TIME = f"{MQTT_BAKERY_PREFIX}/bread_time_update"
MQTT_UPDATE_HAS_CUSTOMER_IN_QUEUE = f"{MQTT_BAKERY_PREFIX}/has_customer_in_queue_update"
MQTT_UPDATE_HAS_UPCOMING_CUSTOMER_IN_QUEUE = f"{MQTT_BAKERY_PREFIX}/has_upcoming_customer_in_queue_update"
MQTT_NEW_TICKET = f"{MQTT_BAKERY_PREFIX}/new_ticket"
MQTT_CALL_CUSTOMER = f"{MQTT_BAKERY_PREFIX}/call_customer"
MQTT_PRINT_TICKET = f"{MQTT_BAKERY_PREFIX}/print_ticket"
MQTT_TICKET_JOB = f"{MQTT_BAKERY_PREFIX}/ticket_job"

mqtt_connected = asyncio.Event()


def _mqtt_log(level: str, event: str, **fields):
    """Emit structured MQTT logs that are easy to filter from docker logs."""
    message = f"mqtt:{event}"
    if level == "warning":
        app_logger.warning(message, extra=fields)
    elif level == "error":
        app_logger.error(message, extra=fields)
    else:
        app_logger.info(message, extra=fields)


async def _publish_with_qos_fallback(client, topic: str, msg: str):
    """Publish with QoS1 first; fallback to QoS0 on timeout to avoid dropped user flow."""
    try:
        _mqtt_log("info", "publish_qos1_attempt", topic=topic, payload=msg)
        await client.publish(topic, msg, qos=1)
        _mqtt_log("info", "publish_qos1_ok", topic=topic)
        return
    except aiomqtt.MqttError as e:
        if "timed out" not in str(e).lower():
            _mqtt_log("warning", "publish_qos1_error", topic=topic, error=str(e))
            raise
        _mqtt_log("warning", "publish_qos1_timeout_retry_qos0", topic=topic, error=str(e))

    _mqtt_log("info", "publish_qos0_attempt", topic=topic, payload=msg)
    await client.publish(topic, msg, qos=0)
    _mqtt_log("info", "publish_qos0_ok", topic=topic)

async def mqtt_handler(app):
    client = app.state.mqtt_client
    while True:
        try:
            async with client:  # This keeps connection alive
                mqtt_connected.set()  # Signal that we're connected
                await client.subscribe("bakery/+/error")
                _mqtt_log("info", "subscribed", topic="bakery/+/error")

                async for message in client.messages:
                    topic = message.topic
                    payload = message.payload.decode()
                    bakery_id = str(topic).split('/')[1]
                    _mqtt_log("warning", "incoming_error_message", topic=str(topic), bakery_id=bakery_id, payload=payload)
                    text = (f"[🔴 MQTT ERROR]:"
                            f"\n\nBakeryID: {bakery_id}"
                            f"\nPayload: {payload}")
                    report_to_admin_api.delay(text, message_thread_id=settings.HARDWARE_CLIENT_ERROR_THREAD_ID)

        except aiomqtt.MqttError as e:
            mqtt_connected.clear()  # Signal disconnection
            # Broker may not be ready yet during startup; avoid noisy error reporting.
            _mqtt_log("warning", "handler_reconnecting_after_mqtt_error", error=str(e))
            await asyncio.sleep(5)
        except Exception as e:
            mqtt_connected.clear()  # Signal disconnection
            await endpoint_helper.log_and_report_error('mqtt_client:mqtt_handler', e)
            await asyncio.sleep(5)


async def safe_publish(request, topic: str, payload: dict) -> bool:
    """Try to publish quickly; never block request flow for long."""
    started_at = time.monotonic()
    try:
        await asyncio.wait_for(mqtt_connected.wait(), timeout=float(settings.MQTT_PUBLISH_TIMEOUT_S))
    except asyncio.TimeoutError:
        _mqtt_log("warning", "skip_publish_not_connected", topic=topic, timeout_s=float(settings.MQTT_PUBLISH_TIMEOUT_S), payload=payload)
        return False

    try:
        msg = json.dumps(payload)
        await asyncio.wait_for(
            _publish_with_qos_fallback(request.app.state.mqtt_client, topic, msg),
            timeout=float(settings.MQTT_PUBLISH_TIMEOUT_S),
        )
        _mqtt_log(
            "info",
            "publish_success",
            topic=topic,
            payload=payload,
            duration_ms=round((time.monotonic() - started_at) * 1000, 2),
        )
        return True
    except asyncio.TimeoutError:
        _mqtt_log("warning", "publish_timeout", topic=topic, timeout_s=float(settings.MQTT_PUBLISH_TIMEOUT_S), payload=payload)
        return False
    except Exception as e:
        _mqtt_log("error", "publish_exception", topic=topic, payload=payload, error=str(e))
        await endpoint_helper.log_and_report_error(f'mqtt_client:safe_publish:{topic}', e)
        return False


async def update_time_per_bread(request, bakery_id, new_config):
    topic = MQTT_UPDATE_BREAD_TIME.format(bakery_id)
    await safe_publish(request, topic, new_config)


async def update_has_customer_in_queue(request, bakery_id, state=True):
    topic = MQTT_UPDATE_HAS_CUSTOMER_IN_QUEUE.format(bakery_id)
    await safe_publish(request, topic, {"state": state})


async def update_has_upcoming_customer_in_queue(request, bakery_id, state=True):
    topic = MQTT_UPDATE_HAS_UPCOMING_CUSTOMER_IN_QUEUE.format(bakery_id)
    await safe_publish(request, topic, {"state": state})


async def notify_new_ticket(request, bakery_id: int, ticket_id: int, token: str):
    topic = MQTT_NEW_TICKET.format(bakery_id)
    await safe_publish(request, topic, {"ticket_id": int(ticket_id), "token": str(token)})


async def call_customer(request, bakery_id: int, ticket_id: int):
    topic = MQTT_CALL_CUSTOMER.format(bakery_id)
    await safe_publish(request, topic, {"ticket_id": int(ticket_id)})


async def print_ticket(request, bakery_id: int, ticket_id: int, token: str):
    topic = MQTT_PRINT_TICKET.format(bakery_id)
    await safe_publish(request, topic, {"bakery_id": int(bakery_id), "ticket_id": int(ticket_id), "token": str(token)})


async def publish_ticket_job(request, bakery_id: int, ticket_id: int, token: str, print_ticket: bool, show_on_display: bool):
    topic = MQTT_TICKET_JOB.format(bakery_id)
    payload = {
        "bakery_id": int(bakery_id),
        "ticket_id": int(ticket_id),
        "token": str(token),
        "print": bool(print_ticket),
        "show_on_display": bool(show_on_display),
    }
    await safe_publish(request, topic, payload)


async def publish_ticket_job_background(bakery_id: int, ticket_id: int, token: str, print_ticket: bool, show_on_display: bool):
    """Publish ticket_job from non-request contexts (e.g., Celery tasks)."""
    topic = MQTT_TICKET_JOB.format(bakery_id)
    payload = {
        "bakery_id": int(bakery_id),
        "ticket_id": int(ticket_id),
        "token": str(token),
        "print": bool(print_ticket),
        "show_on_display": bool(show_on_display),
    }
    try:
        _mqtt_log("info", "background_publish_attempt", topic=topic, payload=payload)
        async with aiomqtt.Client(hostname=settings.MQTT_BROKER_HOST, port=settings.MQTT_BROKER_PORT, timeout=30) as client:
            await asyncio.wait_for(
                _publish_with_qos_fallback(client, topic, json.dumps(payload)),
                timeout=float(settings.MQTT_PUBLISH_TIMEOUT_S),
            )
        _mqtt_log("info", "background_publish_success", topic=topic, payload=payload)
    except aiomqtt.MqttError as e:
        _mqtt_log("warning", "background_publish_mqtt_error", topic=topic, payload=payload, error=str(e))
    except Exception as e:
        _mqtt_log("error", "background_publish_exception", topic=topic, payload=payload, error=str(e))
        await endpoint_helper.log_and_report_error(f'mqtt_client:publish_ticket_job_background:{topic}', e)
