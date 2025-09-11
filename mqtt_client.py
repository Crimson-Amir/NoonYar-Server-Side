import json
import asyncio
from asyncio_mqtt import Client, MqttError
from tasks import report_to_admin_api

MQTT_BAKERY_PREFIX = "bakery/{0}"
MQTT_UPDATE_BREAD_TIME = f"{MQTT_BAKERY_PREFIX}/bread_time_update"

async def mqtt_handler(app):
    client: Client = app.state.mqtt_client

    while True:
        try:
            async with client:  # connect
                async with client.unfiltered_messages() as messages:
                    await client.subscribe("bakery/+/error")
                    async for message in messages:
                        topic = message.topic
                        payload = message.payload.decode()
                        print(f"[MQTT ERROR] {topic}: {payload}")
                        report_to_admin_api.delay(f"[MQTT ERROR] {topic}: {payload}")

        except MqttError as e:
            print(f"[MQTT ERROR] Connection lost: {e}")
            await asyncio.sleep(5)  # wait before retry
        except asyncio.CancelledError:
            print("MQTT handler cancelled")
            break  # exit cleanly


async def update_time_per_bread(request, bakery_id, new_config):
    key = MQTT_UPDATE_BREAD_TIME.format(bakery_id)
    mqqt_payload = json.dumps(new_config)
    await request.app.state.mqtt_client.publish(key, mqqt_payload, qos=1)