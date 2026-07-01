import threading
from collections import deque
from config import HISTORY_MAXLEN, EVENTS_MAXLEN

state_lock = threading.RLock()
serial_lock = threading.Lock()
camera_lock = threading.Lock()
frame_lock = threading.Lock()
detection_lock = threading.Lock()

state = {
    "ph": None,
    "temp": None,
    "turbidity": None,
    "light_lux": None,
    "distance_cm": None,
    "pump": "OFF",
    "peltier": "OFF",
    "air_pump": "OFF",
    "filter_pump": "OFF",
    "rgb": "OFF",
    "rgb_color": "blue",
    "rgb_brightness": 60,
    "gsm_status": "IDLE",
    "mode": "AUTO",
    "connection_source": "Offline",
    "mqtt_connected": False,
    "serial_raw": "No ESP32 data yet",
    "serial_connected": False,
    "camera_connected": False,
    "camera_error": None,
    "camera_updated_at": None,
    "health": 0,
    "updated_at": None,
}

detection_state = {
    "enabled": False,
    "using_supervision": False,
    "count": 0,
    "detections": [],
    "last_latency_ms": None,
    "last_success_at": None,
    "last_error": None,
    "updated_at": None,
}

history = deque(maxlen=HISTORY_MAXLEN)
events = deque(maxlen=EVENTS_MAXLEN)

esp32_serial = None
camera = None
camera_ready = False
camera_error = None
latest_camera_frame = None
latest_raw_frame = None
last_stream_time = 0
last_frame_time = 0
last_crayfish_detection = 0

bbox_annotator = None
label_annotator = None

