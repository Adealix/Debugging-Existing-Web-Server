import time
import threading
import queue
import paho.mqtt.client as mqtt

import state as S
from state import state_lock
from helpers import compute_health, log_event, now_hms
from config import MQTT_ENABLED, MQTT_BROKER, MQTT_PORT, MQTT_BASE_TOPIC
from db import mongo_logger

# ── Module-level clients ─────────────────────────────────────────────────────
# SUBSCRIBER client  -- processes incoming MQTT data (on_message)
# PUBLISHER  client  -- sends outgoing commands ONLY, never blocked by callbacks
_sub_client = None       # subscriber
_pub_client = None       # publisher
_pub_lock = threading.Lock()

# ── Background MongoDB writer queue ───────────────────────────────────────────
# on_message() enqueues write work here instead of blocking on MongoDB.
# A dedicated thread drains this queue and persists to MongoDB.
_db_queue = queue.Queue(maxsize=500)
_db_writer_started = False

_TRACKED_SENSOR_TOPICS = {
    "crayfish/temperature", "crayfish/ph", "crayfish/turbidity",
    "crayfish/lux", "crayfish/distance",
}

def _db_writer_worker():
    while True:
        try:
            task = _db_queue.get()
            kind = task.get("kind")
            if kind == "sensor":
                mongo_logger.insert_sensor(task["data"])
            elif kind == "actuator":
                mongo_logger.insert_actuator(task["data"])
            elif kind == "sms":
                mongo_logger.insert_sms_event(status=task["status"], detail=task["detail"])
            elif kind == "stop":
                break
        except Exception:
            pass

def _ensure_db_writer():
    global _db_writer_started
    if not _db_writer_started:
        _db_writer_started = True
        t = threading.Thread(target=_db_writer_worker, daemon=True, name="db-writer")
        t.start()

def enqueue_sensor_write(data):
    try:
        _db_queue.put_nowait({"kind": "sensor", "data": data})
    except queue.Full:
        pass

def enqueue_actuator_write(data):
    try:
        _db_queue.put_nowait({"kind": "actuator", "data": data})
    except queue.Full:
        pass

def enqueue_sms_write(status, detail):
    try:
        _db_queue.put_nowait({"kind": "sms", "status": status, "detail": detail})
    except queue.Full:
        pass

_prev_gsm = None

def on_message(client, userdata, msg):
    global _prev_gsm
    topic = msg.topic
    try:
        value = msg.payload.decode().strip()
    except Exception:
        return

    with state_lock:
        try:
            if topic == "crayfish/temperature":
                S.state["temp"] = float(value)
            elif topic == "crayfish/ph":
                S.state["ph"] = float(value)
            elif topic == "crayfish/turbidity":
                S.state["turbidity"] = float(value)
            elif topic == "crayfish/lux":
                S.state["light_lux"] = float(value)
            elif topic == "crayfish/distance":
                S.state["distance_cm"] = float(value)
            elif topic == "crayfish/status/pump":
                S.state["pump"] = value.split("|")[0]
            elif topic == "crayfish/status/cooling":
                S.state["peltier"] = value.split("|")[0]
            elif topic == "crayfish/status/airpump":
                S.state["air_pump"] = value.split("|")[0]
            elif topic == "crayfish/status/filter_pump":
                S.state["filter_pump"] = value.split("|")[0]
            elif topic == "crayfish/status/led":
                parts = value.split("|")
                led_state = parts[0]
                S.state["led"] = led_state
                S.state["rgb"] = led_state
                if len(parts) >= 3:
                    try:
                        esp_brightness = int(parts[2])
                        S.state["rgb_brightness"] = int(esp_brightness / 2.55)
                    except ValueError:
                        pass
            elif topic == "crayfish/rgb/brightness":
                S.state["rgb_brightness"] = int(float(value))
            elif topic == "crayfish/status/gsm":
                S.state["gsm_status"] = value
            elif topic == "crayfish/alert":
                log_event("alert", "Crayfish Alert", value)
            elif topic == "crayfish/sensors/batch":
                import json as _json
                try:
                    batch = _json.loads(value)
                    if "ph" in batch:
                        S.state["ph"] = float(batch["ph"])
                    if "temp" in batch:
                        S.state["temp"] = float(batch["temp"])
                    if "turbidity" in batch:
                        S.state["turbidity"] = float(batch["turbidity"])
                    if "lux" in batch:
                        S.state["light_lux"] = float(batch["lux"])
                    if "distance" in batch:
                        S.state["distance_cm"] = float(batch["distance"])
                except Exception:
                    pass

            S.state["health"]           = compute_health(S.state["ph"], S.state["temp"])
            S.state["updated_at"]       = time.time()
            S.state["mqtt_connected"]   = True
            S.state["connection_source"] = "MQTT"

        except Exception as e:
            print(f"[MQTT] Error processing {topic}: {e}")
            return

        tick_time = now_hms()
        tick_ts   = time.time()

        sensor_snapshot = {
            "time":        tick_time,
            "ts":          tick_ts,
            "ph":          S.state["ph"],
            "temp":        S.state["temp"],
            "turbidity":   S.state["turbidity"],
            "light_lux":   S.state["light_lux"],
            "distance_cm": S.state["distance_cm"],
            "health":      S.state["health"],
        }

        actuator_snapshot = {
            "time":           tick_time,
            "ts":             tick_ts,
            "pump":           S.state["pump"],
            "peltier":        S.state["peltier"],
            "air_pump":       S.state["air_pump"],
            "filter_pump":    S.state["filter_pump"],
            "rgb":            S.state.get("rgb"),
            "rgb_brightness": S.state["rgb_brightness"],
            "mode":           S.state.get("mode"),
        }

        current_gsm = S.state["gsm_status"]
        gsm_changed = current_gsm and current_gsm != _prev_gsm

        S.history.append({
            **sensor_snapshot,
            **actuator_snapshot,
            "gsm_status": current_gsm,
        })

    enqueue_sensor_write(sensor_snapshot)
    enqueue_actuator_write(actuator_snapshot)

    if gsm_changed:
        enqueue_sms_write(status=current_gsm, detail=f"topic={topic} value={value}")
        _prev_gsm = current_gsm

def publish(topic, payload, qos=0, retain=False):
    if not MQTT_ENABLED:
        return False
    global _pub_client
    try:
        if _pub_client is None:
            return False
        with _pub_lock:
            _pub_client.publish(topic, payload, qos=qos, retain=retain)
        return True
    except Exception as e:
        print(f"[MQTT] publish error: {e}")
        return False

def _connect_subscriber():
    global _sub_client
    c = mqtt.Client(client_id="crayfish-sub", clean_session=True)
    c.on_message = on_message

    def on_connect(client, userdata, flags, rc):
        print(f"[MQTT] Subscriber connected (rc={rc})")
        try:
            client.subscribe(f"{MQTT_BASE_TOPIC}/#")
        except Exception:
            client.subscribe("crayfish/#")
        with state_lock:
            S.state["mqtt_connected"]    = True
            S.state["connection_source"] = "MQTT"

    def on_disconnect(client, userdata, rc):
        print(f"[MQTT] Subscriber disconnected (rc={rc})")
        with state_lock:
            S.state["mqtt_connected"] = False
            if S.state.get("serial_connected"):
                S.state["connection_source"] = "Serial"
            else:
                S.state["connection_source"] = "Offline"

    c.on_connect    = on_connect
    c.on_disconnect = on_disconnect
    _sub_client = c

def _connect_publisher():
    global _pub_client
    c = mqtt.Client(client_id="crayfish-pub", clean_session=True)

    def on_connect(client, userdata, flags, rc):
        print(f"[MQTT] Publisher connected (rc={rc})")

    c.on_connect = on_connect
    _pub_client = c

def mqtt_worker():
    if not MQTT_ENABLED:
        print("[MQTT] Disabled via configuration.")
        return

    _ensure_db_writer()
    _connect_subscriber()
    _connect_publisher()

    while True:
        try:
            if _pub_client is not None and not _pub_client.is_connected():
                _pub_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
                pub_thread = threading.Thread(
                    target=lambda: _pub_client.loop_forever(),
                    daemon=True, name="mqtt-pub-loop"
                )
                pub_thread.start()

            if _sub_client is not None:
                _sub_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
                _sub_client.loop_forever()
        except Exception as e:
            print(f"[MQTT] Connection failed: {e}. Retrying in 5s...")
            with state_lock:
                S.state["mqtt_connected"] = False
                if S.state.get("serial_connected"):
                    S.state["connection_source"] = "Serial"
                else:
                    S.state["connection_source"] = "Offline"
            time.sleep(5)
