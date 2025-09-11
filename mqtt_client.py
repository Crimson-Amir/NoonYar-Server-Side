import json
import asyncio
import aiomqtt
from tasks import report_to_admin_api
from private import MQTT_BROKER_HOST, MQTT_BROKER_PORT

MQTT_BAKERY_PREFIX = "bakery/{0}"
MQTT_UPDATE_BREAD_TIME = f"{MQTT_BAKERY_PREFIX}/bread_time_update"


async def mqtt_handler(app):
    client: aiomqtt.Client = app.state.mqtt_client

    while True:
        try:
            async with client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT):
                async with client.messages() as messages:
                    await client.subscribe("bakery/+/error")

                    async for message in messages:
                        topic = message.topic
                        payload = message.payload.decode()
                        print(f"[MQTT ERROR] {topic}: {payload}")
                        report_to_admin_api(f"[MQTT ERROR] {topic}: {payload}")

        except aiomqtt.MqttError as e:
            print(f"[MQTT ERROR] Connection lost: {e}")
            await asyncio.sleep(5)

async def update_time_per_bread(request, bakery_id, new_config):
    key = MQTT_UPDATE_BREAD_TIME.format(bakery_id)
    mqqt_payload = json.dumps(new_config)
    await request.app.state.mqtt_client.publish(key, mqqt_payload, qos=1)