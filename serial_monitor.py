import time
import state as S
from state import state_lock, serial_lock
from helpers import parse_serial_line, apply_serial_update, log_event
from config import SERIAL_PORT, SERIAL_BAUDRATE, SERIAL_POLL_INTERVAL, MQTT_BASE_TOPIC

try:
    import serial
except ImportError:
    serial = None

try:
    from mqtt_monitor import publish as mqtt_publish
except Exception:
    mqtt_publish = None


def send_serial_command(command):
    # Try publishing the command over MQTT first (if available).
    try:
        if mqtt_publish is not None:
            topic = f"{MQTT_BASE_TOPIC}/command"
            ok = mqtt_publish(topic, command)
            if ok:
                return True
    except Exception:
        pass

    # Fallback to direct serial write
    if S.esp32_serial is None:
        return False
    try:
        with serial_lock:
            S.esp32_serial.write((command.strip() + "\n").encode("utf-8"))
            S.esp32_serial.flush()
        return True
    except Exception as e:
        log_event("error", "Serial write failed", str(e))
        return False


def esp32_monitor():
    if serial is None:
        print("[ESP32] pyserial not installed.")
        return

    try:
        S.esp32_serial = serial.Serial(SERIAL_PORT, SERIAL_BAUDRATE, timeout=1)
        with state_lock:
            S.state["serial_connected"] = True

        print(f"[ESP32] Listening on {SERIAL_PORT}...")

        while True:
            if S.esp32_serial.in_waiting:
                raw_line = S.esp32_serial.readline().decode("utf-8", errors="ignore").strip()
                if raw_line:
                    print(f"[ESP32] {raw_line}")
                    parsed = parse_serial_line(raw_line)
                    apply_serial_update(parsed, raw_line)
                    log_event("serial", "ESP32 update", raw_line)
            time.sleep(SERIAL_POLL_INTERVAL)

    except Exception as e:
        with state_lock:
            S.state["serial_connected"] = False
        print(f"[ESP32 ERROR] {e}")
        log_event("error", "ESP32 connection error", str(e))

