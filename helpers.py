"""
helpers.py — utility functions for Crayfish IoT System
-------------------------------------------------------
Imports the module-level singleton from db.py — never creates its own instance.
apply_serial_update() fires insert_sensor(), insert_actuator(), and
(on gsm_status change) insert_sms_event() on every serial tick.
MongoDB writes happen OUTSIDE state_lock to avoid blocking the serial thread.
"""

import socket
import time
from datetime import datetime, time as dt_time

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import supervision as sv
except ImportError:
    sv = None

try:
    import numpy as np
except ImportError:
    np = None

import state as S
from state import detection_lock, state_lock
from config import ROBOFLOW_API_URL, ROBOFLOW_MODEL_ID
# ── use the shared singleton, never construct a new MongoLogger here ──────────
from db import mongo_logger


# ──────────────────────────────────────────────────────────────────────────────
# Network / time helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def now_hms():
    return datetime.now().strftime("%I:%M:%S %p")


def parse_time_hhmm(value):
    if isinstance(value, dt_time):
        return value
    try:
        return datetime.strptime(str(value).strip(), "%H:%M").time()
    except Exception:
        return None


def is_within_time_window(start_time, end_time, now=None):
    start = parse_time_hhmm(start_time)
    end   = parse_time_hhmm(end_time)
    if start is None or end is None:
        return False
    if now is None:
        now = datetime.now().time()
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end


# ──────────────────────────────────────────────────────────────────────────────
# Math helpers
# ──────────────────────────────────────────────────────────────────────────────

def clamp(value, low, high):
    return max(low, min(high, value))


def compute_health(ph, temp):
    score = 100
    if ph is not None:
        score -= abs(ph - 7.0) * 18
    else:
        score -= 20
    if temp is not None:
        score -= abs(temp - 27.0) * 10
    else:
        score -= 20
    return int(clamp(score, 0, 100))


# ──────────────────────────────────────────────────────────────────────────────
# Serial parsing
# ──────────────────────────────────────────────────────────────────────────────

def parse_serial_line(line):
    parsed = {
        "ph":             None,
        "temp":           None,
        "turbidity":      None,
        "light_lux":      None,
        "distance_cm":    None,
        "pump":           None,
        "peltier":        None,
        "air_pump":       None,
        "filter_pump":    None,
        "rgb":            None,
        "rgb_color":      None,
        "rgb_brightness": None,
        "gsm_status":     None,
        "mode":           None,
    }
    for part in [p.strip() for p in line.split(",") if p.strip()]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key   = key.strip().lower()
        value = value.strip()
        try:
            if key == "ph":
                parsed["ph"] = float(value)
            elif key == "temp":
                parsed["temp"] = float(value)
            elif key == "turbidity":
                parsed["turbidity"] = float(value)
            elif key in ("lux", "light_lux"):
                parsed["light_lux"] = float(value)
            elif key in ("distance", "distance_cm", "ultrasonic"):
                parsed["distance_cm"] = float(value)
            elif key == "rgb_brightness":
                parsed["rgb_brightness"] = int(float(value))
            elif key in ("pump", "peltier", "air_pump", "filter_pump",
                         "rgb", "gsm_status", "mode"):
                parsed[key] = value.upper()
            elif key == "rgb_color":
                parsed["rgb_color"] = value.lower()
        except Exception:
            continue
    return parsed


# ──────────────────────────────────────────────────────────────────────────────
# In-memory event log (dashboard feed only — NOT persisted)
# ──────────────────────────────────────────────────────────────────────────────

def log_event(kind, title, detail):
    event = {
        "time":   now_hms(),
        "kind":   kind,
        "title":  title,
        "detail": detail,
    }
    with state_lock:
        S.events.appendleft(event)


# ──────────────────────────────────────────────────────────────────────────────
# Detection state helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_detection_snapshot():
    with detection_lock:
        return {
            "enabled":           S.detection_state["enabled"],
            "using_supervision": S.detection_state["using_supervision"],
            "count":             S.detection_state["count"],
            "detections":        list(S.detection_state["detections"]),
            "last_latency_ms":   S.detection_state["last_latency_ms"],
            "last_success_at":   S.detection_state["last_success_at"],
            "last_error":        S.detection_state["last_error"],
            "updated_at":        S.detection_state["updated_at"],
        }


def set_detection_state(**kwargs):
    with detection_lock:
        for key, value in kwargs.items():
            if key in S.detection_state:
                S.detection_state[key] = value


# ──────────────────────────────────────────────────────────────────────────────
# Core state update — saves to MongoDB on every serial tick
# ──────────────────────────────────────────────────────────────────────────────

def apply_serial_update(parsed, raw_line):
    """
    1. Update shared in-memory state (under state_lock).
    2. After releasing the lock, persist to MongoDB:
         • insert_sensor()    → sensor_history   (FIFO 10)
         • insert_actuator()  → actuator_history (FIFO 10)
         • insert_sms_event() → sms_events       (FIFO 10, only on gsm change)
    """
    with state_lock:
        S.state["serial_raw"] = raw_line
        S.state["updated_at"] = time.time()
        changed = False

        # analog sensors
        for field in ("ph", "temp", "turbidity", "light_lux", "distance_cm"):
            if parsed.get(field) is not None:
                S.state[field] = parsed[field]
                changed = True

        # digital actuators — capture previous gsm before overwrite
        prev_gsm = S.state.get("gsm_status")
        for field in ("pump", "peltier", "air_pump", "filter_pump",
                      "rgb", "gsm_status", "mode", "rgb_color", "rgb_brightness"):
            if parsed.get(field) is not None:
                S.state[field] = parsed[field]
                changed = True

        S.state["health"] = compute_health(S.state["ph"], S.state["temp"])

        if not changed:
            return

        # build snapshots while still holding the lock (consistent read)
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
            "rgb":            S.state["rgb"],
            "rgb_color":      S.state["rgb_color"],
            "rgb_brightness": S.state["rgb_brightness"],
            "mode":           S.state["mode"],
        }

        new_gsm    = S.state.get("gsm_status")
        gsm_raw    = raw_line
        gsm_changed = new_gsm and new_gsm != prev_gsm

        # update in-memory history deque (offline fallback)
        S.history.append({**sensor_snapshot, **actuator_snapshot, "gsm_status": new_gsm})

    # ── MongoDB writes OUTSIDE state_lock ────────────────────────────────────
    mongo_logger.insert_sensor(sensor_snapshot)
    mongo_logger.insert_actuator(actuator_snapshot)
    if gsm_changed:
        mongo_logger.insert_sms_event(status=new_gsm, detail=gsm_raw)


# ──────────────────────────────────────────────────────────────────────────────
# Snapshot for /latest endpoint
# ──────────────────────────────────────────────────────────────────────────────

def latest_snapshot():
    detection = get_detection_snapshot()

    with state_lock:
        snapshot = {
            "ph":                S.state["ph"],
            "temp":              S.state["temp"],
            "turbidity":         S.state["turbidity"],
            "light_lux":         S.state["light_lux"],
            "distance_cm":       S.state["distance_cm"],
            "pump":              S.state["pump"],
            "peltier":           S.state["peltier"],
            "air_pump":          S.state["air_pump"],
            "filter_pump":       S.state["filter_pump"],
            "rgb":               S.state["rgb"],
            "rgb_color":         S.state["rgb_color"],
            "rgb_brightness":    S.state["rgb_brightness"],
            "gsm_status":        S.state["gsm_status"],
            "mode":              S.state["mode"],
            "connection_source": S.state["connection_source"],
            "mqtt_connected":    S.state["mqtt_connected"],
            "serial_raw":        S.state["serial_raw"],
            "serial_connected":  S.state["serial_connected"],
            "camera_connected":  S.state["camera_connected"],
            "camera_error":      S.state["camera_error"],
            "camera_updated_at": S.state["camera_updated_at"],
            "detection":         detection,
            "health":            S.state["health"],
            "updated_at":        S.state["updated_at"],
        }

        if snapshot["connection_source"] == "Offline":
            for field in (
                "ph", "temp", "turbidity", "light_lux", "distance_cm",
                "pump", "peltier", "air_pump", "filter_pump",
                "rgb", "rgb_color", "rgb_brightness", "gsm_status"):
                snapshot[field] = None

        in_memory_history = list(S.history)
        events = list(S.events)

    # fetch history from MongoDB; fall back to in-memory deque if unavailable
    if mongo_logger.sensor_col is not None:
        try:
            def _clean(records):
                out = []
                for rec in records:
                    rec = dict(rec)
                    rec["_id"] = str(rec["_id"])
                    if isinstance(rec.get("timestamp"), datetime):
                        rec["timestamp"] = rec["timestamp"].isoformat()
                    out.append(rec)
                return out

            sensor_history   = _clean(mongo_logger.get_sensor_history(10))
            actuator_history = _clean(mongo_logger.get_actuator_history(10))
            sms_history      = _clean(mongo_logger.get_sms_history(10))
        except Exception:
            sensor_history   = in_memory_history
            actuator_history = []
            sms_history      = []
    else:
        sensor_history   = in_memory_history
        actuator_history = []
        sms_history      = []

    snapshot["sensor_history"]   = sensor_history
    snapshot["actuator_history"] = actuator_history
    snapshot["sms_history"]      = sms_history
    snapshot["history"]          = sensor_history  # legacy key
    snapshot["events"]           = events
    return snapshot


# ──────────────────────────────────────────────────────────────────────────────
# Frame / detection helpers (unchanged from original)
# ──────────────────────────────────────────────────────────────────────────────

def downsample_frame(frame, width, height):
    if cv2 is None:
        return frame
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)


def scale_detections(detections, orig_w, orig_h, dw, dh):
    if not detections or orig_w == dw:
        return detections
    scale_x = orig_w / dw
    scale_y = orig_h / dh
    return [{
        "x1": int(d["x1"] * scale_x),
        "y1": int(d["y1"] * scale_y),
        "x2": int(d["x2"] * scale_x),
        "y2": int(d["y2"] * scale_y),
        "confidence": d["confidence"],
        "label":      d["label"],
    } for d in detections]


def draw_detections(frame_bgr, detections):
    if not detections:
        return frame_bgr

    bbox_annotator  = S.bbox_annotator
    label_annotator = S.label_annotator

    if bbox_annotator is not None and sv is not None and np is not None:
        try:
            xyxy     = np.array([[d["x1"], d["y1"], d["x2"], d["y2"]] for d in detections], dtype=np.float32)
            conf     = np.array([d["confidence"] for d in detections], dtype=np.float32)
            class_id = np.arange(len(detections), dtype=np.int32)
            labels   = [f"{d['label']} {d['confidence']:.2f}" for d in detections]
            sv_det   = sv.Detections(xyxy=xyxy, confidence=conf, class_id=class_id)
            annotated = bbox_annotator.annotate(scene=frame_bgr, detections=sv_det)
            if label_annotator is not None:
                annotated = label_annotator.annotate(scene=annotated, detections=sv_det, labels=labels)
            return annotated
        except Exception:
            pass

    for d in detections:
        cv2.rectangle(frame_bgr, (d["x1"], d["y1"]), (d["x2"], d["y2"]), (0, 255, 0), 2)
        cv2.putText(
            frame_bgr,
            f"{d['label']} {d['confidence']:.2f}",
            (d["x1"], max(14, d["y1"] - 8)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA,
        )
    return frame_bgr


def build_roboflow_endpoint():
    base  = ROBOFLOW_API_URL.rstrip("/")
    model = ROBOFLOW_MODEL_ID.strip("/")
    return f"{base}/{model}"


def roboflow_confidence_percent():
    from config import ROBOFLOW_CONFIDENCE
    if ROBOFLOW_CONFIDENCE <= 1.0:
        return max(1.0, min(99.0, ROBOFLOW_CONFIDENCE * 100.0))
    return max(1.0, min(99.0, ROBOFLOW_CONFIDENCE))


def init_supervision_annotators():
    if sv is None or np is None:
        set_detection_state(using_supervision=False)
        return
    try:
        S.bbox_annotator  = sv.BoundingBoxAnnotator() if hasattr(sv, "BoundingBoxAnnotator") else sv.BoxAnnotator()
        S.label_annotator = sv.LabelAnnotator()       if hasattr(sv, "LabelAnnotator")       else None
        set_detection_state(using_supervision=True)
    except Exception:
        S.bbox_annotator  = None
        S.label_annotator = None
        set_detection_state(using_supervision=False)


def run_roboflow_detection(frame_bgr):
    try:
        import requests
    except ImportError:
        raise RuntimeError("requests is not installed")
    if cv2 is None:
        raise RuntimeError("opencv-python is not installed")

    from config import (
        ROBOFLOW_API_KEY, DETECTION_FRAME_WIDTH, DETECTION_FRAME_HEIGHT,
        DETECTION_JPEG_QUALITY, ROBOFLOW_TIMEOUT_SECONDS,
    )

    orig_h, orig_w = frame_bgr.shape[:2]
    downsampled    = downsample_frame(frame_bgr, DETECTION_FRAME_WIDTH, DETECTION_FRAME_HEIGHT)

    ok, jpeg = cv2.imencode(
        ".jpg", downsampled,
        [int(cv2.IMWRITE_JPEG_QUALITY), DETECTION_JPEG_QUALITY],
    )
    if not ok:
        raise RuntimeError("failed to encode frame for inference")

    endpoint = build_roboflow_endpoint()
    params   = {
        "api_key":    ROBOFLOW_API_KEY,
        "confidence": f"{roboflow_confidence_percent():.2f}",
        "format":     "json",
    }

    response = requests.post(
        endpoint,
        params=params,
        files={"file": ("frame.jpg", jpeg.tobytes(), "image/jpeg")},
        timeout=ROBOFLOW_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()

    from config import ROBOFLOW_CONFIDENCE
    detections = []
    for item in payload.get("predictions", []):
        cx, cy = float(item.get("x", 0)), float(item.get("y", 0))
        w, h   = float(item.get("width", 0)), float(item.get("height", 0))
        score  = float(item.get("confidence", 0))
        if score > 1.0:
            score /= 100.0
        if score < ROBOFLOW_CONFIDENCE:
            continue
        detections.append({
            "x1":         max(0, int(round(cx - w / 2))),
            "y1":         max(0, int(round(cy - h / 2))),
            "x2":         max(0, int(round(cx + w / 2))),
            "y2":         max(0, int(round(cy + h / 2))),
            "confidence": round(score, 4),
            "label":      str(item.get("class", "crayfish")),
        })

    return scale_detections(
        detections, orig_w, orig_h,
        DETECTION_FRAME_WIDTH, DETECTION_FRAME_HEIGHT,
    )


def run_yolo_detection(frame_bgr):
    """Run YOLOv8 detection using best.pt model"""
    try:
        from ultralytics import YOLO
    except ImportError:
        raise RuntimeError("ultralytics is not installed")
    
    if cv2 is None:
        raise RuntimeError("opencv-python is not installed")
    
    from config import DETECTION_FRAME_WIDTH, DETECTION_FRAME_HEIGHT, ROBOFLOW_CONFIDENCE
    
    # Load model (cached after first load)
    if not hasattr(run_yolo_detection, 'model'):
        run_yolo_detection.model = YOLO('best.pt')
    
    model = run_yolo_detection.model
    orig_h, orig_w = frame_bgr.shape[:2]
    downsampled = downsample_frame(frame_bgr, DETECTION_FRAME_WIDTH, DETECTION_FRAME_HEIGHT)
    
    # Run inference
    results = model(downsampled, conf=ROBOFLOW_CONFIDENCE)
    
    detections = []
    for r in results:
        for box in r.boxes:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            score = float(box.conf[0])
            detections.append({
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "confidence": round(score, 4),
                "label": "crayfish"
            })
    
    return scale_detections(
        detections, orig_w, orig_h,
        DETECTION_FRAME_WIDTH, DETECTION_FRAME_HEIGHT,
    )