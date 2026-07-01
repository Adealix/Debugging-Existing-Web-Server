import time
import threading
import state as S
from state import frame_lock
from helpers import (
    run_roboflow_detection, set_detection_state,
    log_event
)
from config import (
    ROBOFLOW_ENABLED, ROBOFLOW_DETECTION_INTERVAL,
)

try:
    import requests
except ImportError:
    requests = None
try:
    import cv2
except ImportError:
    cv2 = None

# --------------------------------------------------------------------
# MQTT FEED TRIGGER
# --------------------------------------------------------------------
# Camera-based crayfish detection talks to the ESP32 over MQTT, NOT
# serial. The ESP32 firmware subscribes to TOPIC_CMD_FEEDER
# ("crayfish/cmd/feeder") and runs one counter-clockwise feed cycle
# whenever it receives the payload "FEED_DETECTED" -- regardless of
# whether the feeder is in AUTO or MANUAL mode on that side.
#
# This is the same MQTT client app.py already uses for every other
# actuator command (mode changes, relay ON/OFF, RGB colour, etc.) --
# see `from mqtt_monitor import mqtt_worker, publish as mqtt_publish`
# in app.py. We import the identical function here so detection.py
# publishes through the same connected client instance.
from mqtt_monitor import publish as mqtt_publish

TOPIC_CMD_FEEDER = "crayfish/cmd/feeder"

CRAYFISH_COOLDOWN = 60


def trigger_feed_detected():
    """
    Publish the camera-detected feed trigger to the ESP32 over MQTT.
    The ESP32 firmware applies its own FEED_DETECT_COOLDOWN_MS as a
    second line of defense, but we still gate calls to this function
    with CRAYFISH_COOLDOWN below so we don't spam the broker.
    """
    def _run():
        success = mqtt_publish(TOPIC_CMD_FEEDER, "FEED_DETECTED")
        print(f"[FEEDER] FEED_DETECTED published — {'OK' if success else 'FAILED'}")
        log_event(
            "control",
            "Feed triggered (camera detection)",
            "Published FEED_DETECTED to crayfish/cmd/feeder",
        )
        if not success:
            set_detection_state(
                last_error="Failed to publish FEED_DETECTED over MQTT",
                updated_at=time.time(),
            )

    threading.Thread(target=_run, daemon=True).start()


def trigger_stepper_rotations(count=1, delay=1.0, direction="CW"):
    """
    Manual stepper trigger from the dashboard (/api/control's
    stepper_rotate handler in app.py). Unlike the camera-detection
    path, this is a deliberate operator action, so it is NOT gated by
    CRAYFISH_COOLDOWN and runs `count` times with `delay` seconds in
    between, same as before.

    NOTE ON DIRECTION: the current ESP32 firmware's "FEED" / "MANUAL"
    feed command always rotates counter-clockwise (FEED_STEPS is a
    fixed negative constant on that side) -- it does not yet accept a
    direction over MQTT. The `direction` argument is accepted here so
    the dashboard's CW/CCW buttons keep working without error, and is
    logged for visibility, but it is NOT currently sent to or honoured
    by the ESP32. If you need the dashboard to actually choose
    direction per-press, the firmware needs a new payload format
    (e.g. "FEED|CW" / "FEED|CCW") parsed in mqttCallback() -- let me
    know and I'll wire that up on both sides.
    """
    def _run():
        for i in range(count):
            success = mqtt_publish(TOPIC_CMD_FEEDER, "FEED")
            print(
                f"[STEPPER] Manual rotation {i + 1}/{count} "
                f"(requested direction={direction}, firmware currently always CCW) "
                f"— {'OK' if success else 'FAILED'}"
            )
            log_event(
                "control",
                f"Stepper rotation {i + 1}/{count}",
                f"Published FEED to crayfish/cmd/feeder (requested dir={direction})",
            )
            if not success:
                set_detection_state(
                    last_error="Failed to publish FEED over MQTT",
                    updated_at=time.time(),
                )
            if i < count - 1:
                time.sleep(delay)

    threading.Thread(target=_run, daemon=True).start()


def detection_worker():
    if not ROBOFLOW_ENABLED:
        set_detection_state(
            enabled=False,
            last_error="ROBOFLOW_API_KEY or ROBOFLOW_MODEL_ID is missing",
            updated_at=time.time(),
        )
        return

    set_detection_state(enabled=True, last_error=None, updated_at=time.time())

    last_detection_time = 0

    while True:
        frame = None
        now = time.time()

        if (now - last_detection_time) >= ROBOFLOW_DETECTION_INTERVAL:
            with frame_lock:
                if S.latest_raw_frame is not None:
                    frame = S.latest_raw_frame.copy()

        if frame is None:
            time.sleep(0.1)
            continue

        started = time.time()

        try:
            detections = run_roboflow_detection(frame)

            if len(detections) > 0:
                current_time = time.time()
                if current_time - S.last_crayfish_detection > CRAYFISH_COOLDOWN:
                    print(f"[DETECTION] Crayfish detected! Publishing FEED_DETECTED over MQTT.")
                    log_event(
                        "detection",
                        "Crayfish detected",
                        f"{len(detections)} crayfish — feed triggered via MQTT",
                    )
                    trigger_feed_detected()
                    S.last_crayfish_detection = current_time

            latency_ms = int((time.time() - started) * 1000)
            set_detection_state(
                count=len(detections),
                detections=detections,
                last_latency_ms=latency_ms,
                last_success_at=time.time(),
                last_error=None,
                updated_at=time.time(),
            )
            last_detection_time = time.time()

        except Exception as e:
            set_detection_state(last_error=str(e), updated_at=time.time())
            last_detection_time = time.time()

        elapsed = time.time() - started
        remaining = ROBOFLOW_DETECTION_INTERVAL - elapsed
        if remaining < 0:
            print(
                f"[DETECTION] Warning: detection cycle took {elapsed:.2f}s, "
                f"longer than the configured interval ({ROBOFLOW_DETECTION_INTERVAL}s)."
            )
        time.sleep(max(0.1, remaining))