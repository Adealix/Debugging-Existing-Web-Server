from picamera2 import Picamera2
import cv2

picam2 = Picamera2()

config = picam2.create_preview_configuration(
    main={"size": (1280, 720)}  # use full FOV
)

picam2.configure(config)
picam2.start()

while True:
    frame = picam2.capture_array()

    frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    cv2.imshow("Live Camera", frame)

    if cv2.waitKey(1) == 27:
        break

cv2.destroyAllWindows()