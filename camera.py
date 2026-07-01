import time
import state as S
from state import camera_lock, frame_lock, state_lock
from helpers import draw_detections, get_detection_snapshot, log_event
from config import (
    CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FPS,
    MAX_STREAMING_FPS, STREAM_JPEG_QUALITY
)

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None


def init_camera():
    if S.camera_ready:
        return True

    if Picamera2 is None or cv2 is None:
        S.camera_error = "picamera2 or opencv-python is not installed"
        with state_lock:
            S.state["camera_connected"] = False
            S.state["camera_error"] = S.camera_error
        print(f"[CAMERA] {S.camera_error}")
        return False

    with camera_lock:
        if S.camera_ready:
            return True
        try:
            S.camera = Picamera2()
            config = S.camera.create_video_configuration(
                main={"size": (CAMERA_WIDTH, CAMERA_HEIGHT), "format": "BGR888"},
                controls={"FrameRate": CAMERA_FPS}
            )
            S.camera.configure(config)
            S.camera.set_controls({"AwbEnable": True, "AeEnable": True})
            try:
                sensor_size = S.camera.sensor_resolution
                S.camera.set_controls({"ScalerCrop": (0, 0, sensor_size[0], sensor_size[1])})
            except Exception:
                pass
            S.camera.start()
            time.sleep(2)
            S.camera_ready = True
            S.camera_error = None
            with state_lock:
                S.state["camera_connected"] = True
                S.state["camera_error"] = None
            print("[CAMERA] Initialized (BGR888, corrected color pipeline)")
            return True
        except Exception as e:
            S.camera_error = str(e)
            with state_lock:
                S.state["camera_connected"] = False
                S.state["camera_error"] = S.camera_error
            print(f"[CAMERA ERROR] {e}")
            log_event("error", "Camera init failed", str(e))
            return False


def camera_worker():
    if not init_camera():
        return

    frame_time_target = 1.0 / CAMERA_FPS
    stream_frame_time_target = 1.0 / MAX_STREAMING_FPS

    while True:
        try:
            start = time.time()

            with camera_lock:
                frame = S.camera.capture_array()

            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            with frame_lock:
                S.latest_raw_frame = frame

            now = time.time()
            if (now - S.last_stream_time) >= stream_frame_time_target:
                detections = get_detection_snapshot()["detections"]
                display_frame = frame.copy() if detections else frame
                if detections:
                    display_frame = draw_detections(display_frame, detections)

                ok, jpeg = cv2.imencode(".jpg", display_frame,
                                        [int(cv2.IMWRITE_JPEG_QUALITY), STREAM_JPEG_QUALITY])
                if ok:
                    S.latest_camera_frame = jpeg.tobytes()
                    S.last_stream_time = now
                    with state_lock:
                        S.state["camera_connected"] = True
                        S.state["camera_error"] = None
                        S.state["camera_updated_at"] = time.time()

            elapsed = time.time() - start
            time.sleep(max(frame_time_target - elapsed, 0.001))

        except Exception as e:
            with state_lock:
                S.state["camera_connected"] = False
                S.state["camera_error"] = str(e)
            log_event("error", "Camera capture failed", str(e))
            time.sleep(1)


def generate_camera_stream():
    frame_send_delay = 1.0 / MAX_STREAMING_FPS
    last_send = 0

    while True:
        frame = S.latest_camera_frame
        now = time.time()
        if frame and (now - last_send) >= frame_send_delay:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
            )
            last_send = now
            time.sleep(0.001)
        else:
            time.sleep(0.01)

