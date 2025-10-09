import json
import asyncio
from application.tasks import report_to_admin_api
from application.helpers import endpoint_helper
from application.setting import settings

MQTT_BAKERY_PREFIX = "bakery/{0}"
MQTT_UPDATE_BREAD_TIME = f"{MQTT_BAKERY_PREFIX}/bread_time_update"
MQTT_UPDATE_HAS_CUSTOMER_IN_QUEUE = f"{MQTT_BAKERY_PREFIX}/has_customer_in_queue_update"
MQTT_UPDATE_HAS_UPCOMING_CUSTOMER_IN_QUEUE = f"{MQTT_BAKERY_PREFIX}/has_upcoming_customer_in_queue_update"

mqtt_connected = asyncio.Event()

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

        except Exception as e:
            mqtt_connected.clear()  # Signal disconnection
            endpoint_helper.log_and_report_error('mqtt_client:mqtt_handler', e)
            await asyncio.sleep(5)


async def safe_publish(request, topic: str, payload: dict):
    """Waits for MQTT connection and publishes a payload safely, logging any errors."""
    try:
        await mqtt_connected.wait()
        msg = json.dumps(payload)
        await request.app.state.mqtt_client.publish(topic, msg, qos=1)
    except Exception as e:
        endpoint_helper.log_and_report_error(f'mqtt_client:safe_publish:{topic}', e)


async def update_time_per_bread(request, bakery_id, new_config):
    topic = MQTT_UPDATE_BREAD_TIME.format(bakery_id)
    await safe_publish(request, topic, new_config)


async def update_has_customer_in_queue(request, bakery_id, state=True):
    topic = MQTT_UPDATE_HAS_CUSTOMER_IN_QUEUE.format(bakery_id)
    await safe_publish(request, topic, {"state": state})


async def update_has_upcoming_customer_in_queue(request, bakery_id, state=True):
    topic = MQTT_UPDATE_HAS_UPCOMING_CUSTOMER_IN_QUEUE.format(bakery_id)
    await safe_publish(request, topic, {"state": state})
