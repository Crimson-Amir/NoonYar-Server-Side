import json
import threading
import paho.mqtt.client as mqtt
from tasks import register_new_customer, next_ticket_process
from helpers.token_helpers import verify_bakery_token

BROKER_HOST = "localhost"
BROKER_PORT = 1883

def on_connect(client, userdata, flags, rc):
    print(f"Connected to MQTT broker with result code {rc}")
    # Subscribe to new customer and next ticket topics for all bakeries
    client.subscribe("bakery/+/nc")
    client.subscribe("bakery/+/nt")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        print(f"Received on {msg.topic}: {payload}")

        # topic pattern: bakery/{bakery_id}/nc
        parts = msg.topic.split("/")
        bakery_id = int(parts[1])
        command = parts[2]

        token = payload.get("token")
        if not token or not verify_bakery_token(token, bakery_id):
            print("Invalid token")
            return

        if command == "nc":
            register_new_customer.delay(
                payload["hardware_customer_id"],
                bakery_id,
                payload["bread_requirements"]
            )
        elif command == "nt":
            next_ticket_process.delay(
                payload["current_customer_id"],
                bakery_id
            )

    except Exception as e:
        print(f"Error handling message: {e}")

def start_mqtt():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(BROKER_HOST, BROKER_PORT, 60)
    client.loop_start()  # non-blocking
