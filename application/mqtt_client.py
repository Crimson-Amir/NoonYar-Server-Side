import json
import asyncio
import logging
from application.tasks import report_to_admin_api
from application.helpers import endpoint_helper
from application.setting import settings
import aiomqtt

MQTT_BAKERY_PREFIX = "bakery/{0}"
MQTT_UPDATE_BREAD_TIME = f"{MQTT_BAKERY_PREFIX}/bread_time_update"
MQTT_UPDATE_HAS_CUSTOMER_IN_QUEUE = f"{MQTT_BAKERY_PREFIX}/has_customer_in_queue_update"
MQTT_UPDATE_HAS_UPCOMING_CUSTOMER_IN_QUEUE = f"{MQTT_BAKERY_PREFIX}/has_upcoming_customer_in_queue_update"
MQTT_NEW_TICKET = f"{MQTT_BAKERY_PREFIX}/new_ticket"
MQTT_CALL_CUSTOMER = f"{MQTT_BAKERY_PREFIX}/call_customer"
MQTT_PRINT_TICKET = f"{MQTT_BAKERY_PREFIX}/print_ticket"
MQTT_TICKET_JOB = f"{MQTT_BAKERY_PREFIX}/ticket_job"

mqtt_connected = asyncio.Event()
logger = logging.getLogger(__name__)


async def _publish_with_qos_fallback(client, topic: str, msg: str):
    """Publish with QoS1 first; fallback to QoS0 on timeout to avoid dropped user flow."""
    try:
        await client.publish(topic, msg, qos=1)
        return
    except aiomqtt.MqttError as e:
        if "timed out" not in str(e).lower():
            raise
        logger.warning("mqtt publish QoS1 timed out, retrying QoS0 topic=%s", topic)

    await client.publish(topic, msg, qos=0)

async def mqtt_handler(app):
    client = app.state.mqtt_client
    while True:
        try:
            async with client:  # This keeps connection alive
                mqtt_connected.set()  # Signal that we're connected
                await client.subscribe("bakery/+/error")

                async for message in client.messages:
                    topic = message.topic
                    payload = message.payload.decode()
                    bakery_id = str(topic).split('/')[1]
                    text = (f"[🔴 MQTT ERROR]:"
                            f"\n\nBakeryID: {bakery_id}"
                            f"\nPayload: {payload}")
                    report_to_admin_api.delay(text, message_thread_id=settings.HARDWARE_CLIENT_ERROR_THREAD_ID)

        except aiomqtt.MqttError as e:
            mqtt_connected.clear()  # Signal disconnection
            # Broker may not be ready yet during startup; avoid noisy error reporting.
            logger.warning("mqtt_client:mqtt_handler reconnecting after mqtt error: %s", e)
            await asyncio.sleep(5)
        except Exception as e:
            mqtt_connected.clear()  # Signal disconnection
            await endpoint_helper.log_and_report_error('mqtt_client:mqtt_handler', e)
            await asyncio.sleep(5)


async def safe_publish(request, topic: str, payload: dict) -> bool:
    """Try to publish quickly; never block request flow for long."""
    try:
        await asyncio.wait_for(mqtt_connected.wait(), timeout=float(settings.MQTT_PUBLISH_TIMEOUT_S))
    except asyncio.TimeoutError:
        logger.warning("mqtt not connected within timeout, skip publish topic=%s", topic)
        return False

    try:
        msg = json.dumps(payload)
        await asyncio.wait_for(
            _publish_with_qos_fallback(request.app.state.mqtt_client, topic, msg),
            timeout=float(settings.MQTT_PUBLISH_TIMEOUT_S),
        )
        return True
    except asyncio.TimeoutError:
        logger.warning("mqtt publish timed out after %.2fs topic=%s", float(settings.MQTT_PUBLISH_TIMEOUT_S), topic)
        return False
    except Exception as e:
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
        async with aiomqtt.Client(hostname=settings.MQTT_BROKER_HOST, port=settings.MQTT_BROKER_PORT, timeout=30) as client:
            await asyncio.wait_for(
                _publish_with_qos_fallback(client, topic, json.dumps(payload)),
                timeout=float(settings.MQTT_PUBLISH_TIMEOUT_S),
            )
    except aiomqtt.MqttError as e:
        logger.warning("mqtt_client:publish_ticket_job_background failed (mqtt): %s", e)
    except Exception as e:
        await endpoint_helper.log_and_report_error(f'mqtt_client:publish_ticket_job_background:{topic}', e)
