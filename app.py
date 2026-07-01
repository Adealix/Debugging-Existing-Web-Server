"""
app.py — Crayfish IoT System entry point
-----------------------------------------
CRITICAL ORDER:
 1. Load .env FIRST — before any other local import — so DB_URI is in
    os.environ when db.py's module-level singleton is constructed.
 2. Import db (triggers MongoLogger init with the correct URI).
 3. Import everything else.

This file is the ONLY entry point.  server.py is no longer needed.
"""

# ── 1. Load environment variables before anything else ───────────────────────
import os
from dotenv import load_dotenv
load_dotenv()  # reads .env → sets DB_URI, etc. in os.environ

# ── 2. Standard library ───────────────────────────────────────────────────────
import threading
import time

# ── 3. db singleton (constructed here with the now-populated DB_URI) ─────────
from db import mongo_logger  # noqa: F401  — import triggers connection + ping

# ── 4. Everything else ────────────────────────────────────────────────────────
from flask import Flask, Response, jsonify, request, redirect, send_from_directory

from helpers import (
    get_local_ip, init_supervision_annotators,
    latest_snapshot, live_snapshot, history_snapshot,
    get_detection_snapshot, log_event,
)
from serial_monitor import esp32_monitor, send_serial_command
from camera import camera_worker, generate_camera_stream, init_camera
from detection import detection_worker, trigger_stepper_rotations
from config import (
    ROBOFLOW_ENABLED, DETECTION_FRAME_WIDTH, DETECTION_FRAME_HEIGHT,
    DETECTION_JPEG_QUALITY, STREAM_JPEG_QUALITY, MAX_STREAMING_FPS,
    FRAME_SKIP, ROBOFLOW_DETECTION_INTERVAL, STEPPER_ROTATIONS,
    STEPPER_ROTATION_DELAY, SERIAL_PORT, SERIAL_BAUDRATE, FLASK_PORT,
)
from ngrok_tunnel import ngrok_worker, get_tunnel_url
from mqtt_monitor import mqtt_worker, publish as mqtt_publish
import state as S

app = Flask(
    __name__,
    static_folder=os.path.join(os.path.dirname(__file__), "frontend", "static"),
    static_url_path="/static"
)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 300  # cache static files 5 min

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def home():
    return redirect("/dashboard")


@app.route("/dashboard")
def dashboard():
    return send_from_directory(FRONTEND_DIR, "dashboard.html")


@app.route("/maranan")
def maranan():
    return send_from_directory(FRONTEND_DIR, "maranan.html")


@app.route("/calungsod")
def calungsod():
    return send_from_directory(FRONTEND_DIR, "calungsod.html")


@app.route("/garcia")
def garcia():
    return send_from_directory(FRONTEND_DIR, "garcia.html")


@app.route("/canta")
def canta():
    return send_from_directory(FRONTEND_DIR, "canta.html")


@app.route("/delrosario")
def delrosario():
    return send_from_directory(FRONTEND_DIR, "delrosario.html")


@app.route("/famini")
def famini():
    return send_from_directory(FRONTEND_DIR, "famini.html")




@app.route("/latest")
def latest():
    return jsonify(latest_snapshot())


@app.route("/api/live")
def api_live():
    """Fast path: live sensor/actuator values only — ~1 KB JSON."""
    return jsonify(live_snapshot())


@app.route("/api/history")
def api_history():
    """History + events for charts — polled less frequently."""
    return jsonify(history_snapshot())


@app.route("/api/data")
def api_data():
    """Legacy full-snapshot endpoint (live + history)."""
    return jsonify(latest_snapshot())


@app.route("/esp32")
def esp32():
    with S.state_lock:
        return jsonify({
            "raw":       S.state["serial_raw"],
            "connected": S.state["serial_connected"],
        })


@app.route("/camera")
def camera_status():
    detection = get_detection_snapshot()
    with S.state_lock:
        return jsonify({
            "connected":  S.state["camera_connected"],
            "error":      S.state["camera_error"],
            "updated_at": S.state["camera_updated_at"],
            "detection":  detection,
        })


@app.route("/api/detections")
def api_detections():
    return jsonify(get_detection_snapshot())


@app.route("/camera/feed")
def camera_feed():
    if not S.camera_ready and not init_camera():
        return jsonify({"error": "camera unavailable"}), 503
    return Response(
        generate_camera_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/ngrok")
def ngrok_status():
    url = get_tunnel_url()
    return jsonify({
        "tunnel_url": url,
        "active":     url is not None,
        "dashboard":  f"{url}/dashboard" if url else None,
    })


# ── History endpoints ─────────────────────────────────────────────────────────

@app.route("/history")
@app.route("/sensor_history")
def sensor_history():
    """Last 10 analog sensor readings (from in-memory deque — fast)."""
    with S.state_lock:
        records = list(S.history)[:10]
    return jsonify(records)


@app.route("/actuator_history")
def actuator_history():
    """Last 10 digital actuator snapshots (from in-memory deque — fast)."""
    with S.state_lock:
        records = list(S.history)[:10]
    return jsonify(records)


@app.route("/sms_history")
def sms_history():
    """Last 10 GSM / SMS events."""
    return jsonify([])


# ── Control endpoint ──────────────────────────────────────────────────────────

@app.route("/api/control", methods=["POST"])
def api_control():
    data = request.json or {}
    commands = []
    rejected = []        # commands ignored because the system isn't in MANUAL mode
    mqtt_messages = []  # (topic, payload) — published to the ESP32 after we release the lock

    # Maps the dashboard's actuator keys to the ESP32 firmware's MQTT actuator names.
    # NOTE: filter_pump has no corresponding relay/pin on the current ESP32 sketch,
    # so it updates the dashboard state but does not drive any hardware yet.
    ESP32_ACTUATOR = {
        "pump":     "pump",
        "air_pump": "airpump",
        "peltier":  "cooling",
        "rgb":      "led",
    }

    with S.state_lock:

        # ── Mode ──────────────────────────────────────────────────────────────
        if "mode" in data:
            mode = str(data["mode"]).upper()
            if mode in ("AUTO", "MANUAL"):
                S.state["mode"] = mode
                print("MODE SET BY DASHBOARD:", S.state["mode"])
                commands.append(f"MODE={mode}")
                log_event("control", "Mode changed", f"Mode set to {mode}")
                # The ESP32 tracks AUTO/MANUAL per actuator over MQTT, not over
                # Serial — broadcast the new mode to every actuator it knows
                # about so manual control actually takes effect on the hardware.
                for esp32_name in ("airpump", "pump", "cooling", "led"):
                    mqtt_messages.append((f"crayfish/mode/{esp32_name}", mode))

        # The system-wide mode AFTER the block above has had a chance to change
        # it. Everything below this point that represents a *manual override*
        # (relay ON/OFF, RGB colour/brightness) is only honoured while this is
        # MANUAL — this is the API-level half of the override guarantee; the
        # ESP32 firmware enforces the same rule again on its side, so a stray
        # request (bypassing the dashboard's UI lock) can never desync the
        # automatic, sensor-driven state from what the dashboard displays.
        effective_mode = S.state.get("mode", "AUTO")
        manual_active = effective_mode == "MANUAL"

        # ── Helper: generic ON/OFF/TOGGLE relay ───────────────────────────────
        def _relay(key, serial_key=None):
            if key not in data:
                return
            serial_key = serial_key or key.upper()
            raw = data[key]

            if raw is True:
                val = "ON"
            elif raw is False:
                val = "OFF"
            else:
                val = str(raw).upper()
            esp32_name = ESP32_ACTUATOR.get(key)
            
            # Allow OFF commands to work in both AUTO and MANUAL modes (safety override)
            # ON/TOGGLE commands still require MANUAL mode
            if not manual_active and val != "OFF":
                rejected.append(key)
                return

            if val in ("ON", "OFF"):
                S.state[key] = val
                commands.append(f"{serial_key}={val}")
                log_event("control", f"{key.replace('_',' ').title()} updated",
                          f"{key} set to {val}")
                if esp32_name:
                    mqtt_messages.append((f"crayfish/cmd/{esp32_name}", val))
            elif val == "TOGGLE":
                new_val = "OFF" if S.state.get(key) == "ON" else "ON"
                S.state[key] = new_val
                commands.append(f"{serial_key}={new_val}")
                log_event("control", f"{key.replace('_',' ').title()} toggled",
                          f"{key} set to {new_val}")
                if esp32_name:
                    mqtt_messages.append((f"crayfish/cmd/{esp32_name}", new_val))

        # ── All relays ────────────────────────────────────────────────────────
        _relay("pump",        "PUMP")
        _relay("air_pump",    "AIR_PUMP")
       # _relay("filter_pump", "FILTER_PUMP")
        _relay("peltier",     "PELTIER")
        _relay("rgb",         "RGB")

        # ── RGB colour ────────────────────────────────────────────────────────
        if "rgb_color" in data:
            if not manual_active:
                rejected.append("rgb_color")
            else:
                colour = str(data["rgb_color"]).lower()
                valid_colours = ("blue", "cyan", "purple", "white", "red", "green", "yellow")
                if colour in valid_colours:
                    S.state["rgb_color"] = colour
                    commands.append(f"RGB_COLOR={colour.upper()}")
                    log_event("control", "RGB colour changed", f"Colour set to {colour}")
                    mqtt_messages.append(("crayfish/cmd/rgb_color", colour.upper()))

        # ── RGB brightness ────────────────────────────────────────────────────
        if "rgb_brightness" in data:
            if not manual_active:
                rejected.append("rgb_brightness")
            else:
                try:
                    brightness = max(0, min(100, int(data["rgb_brightness"])))
                    S.state["rgb_brightness"] = brightness
                    commands.append(f"RGB_BRIGHTNESS={brightness}")
                    log_event("control", "RGB brightness changed",
                              f"Brightness set to {brightness}%")
                    mqtt_messages.append(("crayfish/cmd/rgb_brightness", str(brightness)))
                except (ValueError, TypeError):
                    pass

        # ── GSM / SMS alert ───────────────────────────────────────────────────
        # Not gated by mode — a test alert is a one-off action, not an
        # automatic-vs-manual actuator override.
        if "gsm_alert" in data:
            alert_msg = str(data["gsm_alert"])
            S.state["gsm_status"] = "SENDING"
            commands.append(f"GSM_ALERT={alert_msg}")
            log_event("sms", "GSM alert triggered",
                      f"Alert payload: {alert_msg}")

    # ── Send all commands to ESP32 over serial (logging / legacy path) ────────
    #for cmd in commands:
       #send_serial_command(cmd)

    # ── Send all commands to ESP32 over MQTT (the path it actually listens to) ─
    for topic, payload in mqtt_messages:
        mqtt_publish(topic, payload)

    # ── Stepper (runs outside the lock — uses its own thread) ─────────────────
    if data.get("stepper_rotate"):
        rotations = int(data.get("stepper_rotations", STEPPER_ROTATIONS))
        direction = str(data.get("stepper_direction", "CW")).upper()
        log_event("control", "Manual stepper trigger",
                  f"Rotating {direction} x{rotations}")
        trigger_stepper_rotations(
            count=rotations,
            delay=STEPPER_ROTATION_DELAY,
            direction=direction,          # pass direction if your impl supports it
        )
        commands.append(f"STEPPER_ROTATE {direction} x{rotations}")

    response = {"status": "ok", "sent": commands, "state": live_snapshot()}
    if rejected:
        response["status"] = "partial" if commands else "ignored"
        response["rejected"] = rejected
        response["reason"] = "System is in AUTO mode — switch to Manual to control actuators."
    return jsonify(response)


# ──────────────────────────────────────────────────────────────────────────────
# Flask runner
# ──────────────────────────────────────────────────────────────────────────────

def run_flask():
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, threaded=True, use_reloader=False)


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ip = get_local_ip()
    init_supervision_annotators()

    db_status = "CONNECTED" if mongo_logger.sensor_col is not None else "OFFLINE (check .env / network)"

    print("\n==============================")
    print("CRAYFISH IOT SYSTEM STARTING")
    print("==============================")
    print(f"Dashboard  : http://{ip}:{FLASK_PORT}/dashboard")
    print(f"API        : http://{ip}:{FLASK_PORT}/api/data")
    print(f"Detection  : {'ENABLED' if ROBOFLOW_ENABLED else 'DISABLED'}")
    print(f"Camera     : http://{ip}:{FLASK_PORT}/camera/feed")
    print(f"MongoDB    : {db_status}")
    print("==============================")
    print("\n  HISTORY ENDPOINTS:")
    print(f"  /sensor_history    — last 10 sensor readings")
    print(f"  /actuator_history  — last 10 actuator states")
    print(f"  /sms_history       — last 10 SMS/GSM events")
    print("==============================")
    print("\n  PERFORMANCE SETTINGS:")
    print(f"  Detection resolution : {DETECTION_FRAME_WIDTH}x{DETECTION_FRAME_HEIGHT}")
    print(f"  Detection JPEG quality: {DETECTION_JPEG_QUALITY}%")
    print(f"  Stream JPEG quality  : {STREAM_JPEG_QUALITY}%")
    print(f"  Stream FPS cap       : {MAX_STREAMING_FPS}")
    print(f"  Frame skip           : Every {FRAME_SKIP} frame(s)")
    print(f"  Detection interval   : {ROBOFLOW_DETECTION_INTERVAL}s")
    print(f"  Stepper rotations    : {STEPPER_ROTATIONS}")
    print(f"  Stepper delay        : {STEPPER_ROTATION_DELAY}s")
    print("==============================\n")

    threading.Thread(target=camera_worker,    daemon=True).start()
    threading.Thread(target=detection_worker, daemon=True).start()
    threading.Thread(target=mqtt_worker,      daemon=True).start()
    threading.Thread(target=esp32_monitor,    daemon=True).start()
    threading.Thread(target=ngrok_worker,     daemon=True).start()
    threading.Thread(target=run_flask,        daemon=True).start()

    while True:
        time.sleep(1)


