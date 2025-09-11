import json
import asyncio
import aiomqtt
from tasks import report_to_admin_api
from private import MQTT_BROKER_HOST, MQTT_BROKER_PORT

MQTT_BAKERY_PREFIX = "bakery/{0}"
MQTT_UPDATE_BREAD_TIME = f"{MQTT_BAKERY_PREFIX}/bread_time_update"


async def mqtt_handler(app):
    client = app.state.mqtt_client
    print(f"[MQTT Handler] Starting MQTT handler task...")

    while True: # Keep trying to connect if disconnected
        try:
            print(f"[MQTT Handler] Attempting to connect")
            async with client:  # <-- this establishes the connection
                print(f"[MQTT Handler] Connected to MQTT broker. Subscribing to...")
                await client.subscribe("bakery/+/error")
                print(f"[MQTT Handler] Subscribed. Waiting for messages...")

                async with client.unfiltered_messages() as messages:
                    async for message in messages:
                        topic = message.topic
                        payload = message.payload.decode()
                        print(f"[MQTT ERROR RECEIVED] {topic}: {payload}") # Clearly indicate receipt
                        # report_to_admin_api(f"[MQTT ERROR] {topic}: {payload}") # Re-enable when confident

        except Exception as e: # Catch any other unexpected exceptions
            print(f"[MQTT Handler] UNEXPECTED ERROR: {e}. Retrying in 5 seconds...")
            await asyncio.sleep(5)

async def update_time_per_bread(request, bakery_id, new_config):
    key = MQTT_UPDATE_BREAD_TIME.format(bakery_id)
    mqqt_payload = json.dumps(new_config)
    await request.app.state.mqtt_client.publish(key, mqqt_payload, qos=1)