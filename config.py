import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyUSB0")
SERIAL_BAUDRATE = int(os.getenv("SERIAL_BAUDRATE", "115200"))
CAMERA_WIDTH = int(os.getenv("CAMERA_WIDTH", "960"))
CAMERA_HEIGHT = int(os.getenv("CAMERA_HEIGHT", "540"))
CAMERA_FPS = int(os.getenv("CAMERA_FPS", "12"))
ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_API_KEY", "").strip()
ROBOFLOW_MODEL_ID = os.getenv("ROBOFLOW_MODEL_ID", "").strip()
ROBOFLOW_API_URL = os.getenv("ROBOFLOW_API_URL", "https://serverless.roboflow.com").strip()
ROBOFLOW_CONFIDENCE = float(os.getenv("ROBOFLOW_CONFIDENCE", "0.35"))
ROBOFLOW_DETECTION_INTERVAL = float(os.getenv("ROBOFLOW_DETECTION_INTERVAL", "2.0"))
ROBOFLOW_TIMEOUT_SECONDS = float(os.getenv("ROBOFLOW_TIMEOUT_SECONDS", "8.0"))
ROBOFLOW_ENABLED = bool(ROBOFLOW_API_KEY and ROBOFLOW_MODEL_ID)
FLASK_PORT = int(os.getenv("FLASK_PORT", "5005"))
DB_URI = os.getenv("DB_URI")


DETECTION_FRAME_WIDTH = int(os.getenv("DETECTION_FRAME_WIDTH", "320"))
DETECTION_FRAME_HEIGHT = int(os.getenv("DETECTION_FRAME_HEIGHT", "320"))
STREAM_JPEG_QUALITY = int(os.getenv("STREAM_JPEG_QUALITY", "50"))
DETECTION_JPEG_QUALITY = int(os.getenv("DETECTION_JPEG_QUALITY", "45"))
FRAME_SKIP = int(os.getenv("FRAME_SKIP", "2"))
MAX_STREAMING_FPS = int(os.getenv("MAX_STREAMING_FPS", "8"))
SERIAL_POLL_INTERVAL = float(os.getenv("SERIAL_POLL_INTERVAL", "0.1"))
HISTORY_MAXLEN = int(os.getenv("HISTORY_MAXLEN", "30"))
EVENTS_MAXLEN = int(os.getenv("EVENTS_MAXLEN", "15"))
STEPPER_ROTATIONS = int(os.getenv("STEPPER_ROTATIONS", "2"))
STEPPER_ROTATION_DELAY = float(os.getenv("STEPPER_ROTATION_DELAY", "0.5"))

# MQTT configuration
MQTT_ENABLED = bool(int(os.getenv("MQTT_ENABLED", "1")))
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "crayfish")

