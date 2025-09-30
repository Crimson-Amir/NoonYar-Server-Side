import json
import asyncio
from tasks import report_to_admin_api
from helpers import endpoint_helper
from setting import settings

MQTT_BAKERY_PREFIX = "bakery/{0}"
MQTT_UPDATE_BREAD_TIME = f"{MQTT_BAKERY_PREFIX}/bread_time_update"
MQTT_UPDATE_HAS_CUSTOMER_IN_QUEUE = f"{MQTT_BAKERY_PREFIX}/has_customer_in_queue_update"
MQTT_UPDATE_HAS_UPCOMING_CUSTOMER_IN_QUEUE = f"{MQTT_BAKERY_PREFIX}/has_upcoming_customer_in_queue_update"

async def mqtt_handler(app):
    client = app.state.mqtt_client
    while True:
        try:
            async with client:  # connect using hostname/port from init
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
            endpoint_helper.log_and_report_error('mqtt_client:mqtt_handler', e)
            await asyncio.sleep(5)

async def update_time_per_bread(request, bakery_id, new_config):
    key = MQTT_UPDATE_BREAD_TIME.format(bakery_id)
    mqqt_payload = json.dumps(new_config)
    await request.app.state.mqtt_client.publish(key, mqqt_payload, qos=1)

async def update_has_customer_in_queue(request, bakery_id, state=True):
    key = MQTT_UPDATE_HAS_CUSTOMER_IN_QUEUE.format(bakery_id)
    mqqt_payload = json.dumps({"state": state})
    await request.app.state.mqtt_client.publish(key, mqqt_payload, qos=1)

async def update_has_upcoming_customer_in_queue(request, bakery_id, state=True):
    key = MQTT_UPDATE_HAS_UPCOMING_CUSTOMER_IN_QUEUE.format(bakery_id)
    mqqt_payload = json.dumps({"state": state})
    await request.app.state.mqtt_client.publish(key, mqqt_payload, qos=1)