import paho.mqtt.client as mqtt
import state as S
from state import state_lock
from helpers import compute_health, log_event, now_hms
import time

BROKER = "localhost"

def on_message(client, userdata, msg):
    topic = msg.topic
    value = msg.payload.decode().strip()

    with state_lock:
        if topic == "crayfish/temperature":
            S.state["temp"] = float(value)
        elif topic == "crayfish/ph":
            S.state["ph"] = float(value)
        elif topic == "crayfish/turbidity":
            S.state["turbidity"] = float(value)
        elif topic == "crayfish/status/pump":
            S.state["pump"] = value.split("|")[0]
        elif topic == "crayfish/status/cooling":
            S.state["peltier"] = value.split("|")[0]
        S.state["health"] = compute_health(S.state["ph"], S.state["temp"])
        S.state["updated_at"] = time.time()
        S.state["serial_connected"] = True

def mqtt_worker():
    client = mqtt.Client()
    client.on_message = on_message
    client.connect(BROKER, 1883)
    client.subscribe("crayfish/#")
    client.loop_forever()