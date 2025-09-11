import json
import asyncio
from tasks import report_to_admin_api
from private import HARDWARE_CLIENT_ERROR_THREAD_ID

MQTT_BAKERY_PREFIX = "bakery/{0}"
MQTT_UPDATE_BREAD_TIME = f"{MQTT_BAKERY_PREFIX}/bread_time_update"

async def mqtt_handler(app):
    client = app.state.mqtt_client
    while True:
        try:
            async with client:  # connect using hostname/port from init
                await client.subscribe("bakery/+/error")

                async for message in client.messages:
                    topic = message.topic
                    payload = message.payload.decode()
                    report_to_admin_api.delay(f"[MQTT ERROR] {topic}: {payload}", message_thread_id=HARDWARE_CLIENT_ERROR_THREAD_ID)

        except Exception as e:
            await asyncio.sleep(5)

async def update_time_per_bread(request, bakery_id, new_config):
    key = MQTT_UPDATE_BREAD_TIME.format(bakery_id)
    mqqt_payload = json.dumps(new_config)
    await request.app.state.mqtt_client.publish(key, mqqt_payload, qos=1)