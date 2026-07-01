from picamera2 import Picamera2
import cv2
import time

picam2 = Picamera2()

config = picam2.create_still_configuration()
picam2.configure(config)

picam2.start()
time.sleep(1)

image = picam2.capture_array()

cv2.imwrite("test.jpg", image)

picam2.stop()

print("Image saved as test.jpg")