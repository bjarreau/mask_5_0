from flask import Flask, Response, request, render_template
from streamer import VideoStreamer
from VideoStream import VideoStream
from LinkedStream import LinkedStream
import cv2
import zmq
import pafy
import numpy as np
import threading
import time
from os import getenv
import os
from dotenv import load_dotenv
from tensorflow.keras.applications.mobilenet_v2 import preprocess_input
from tensorflow.keras.preprocessing.image import img_to_array
from tensorflow.keras.models import load_model

load_dotenv()
outframe = None
lock = threading.Lock()
livestream = VideoStream().start()
linkedstream = None
time.sleep(2.0)

#defaults
active = "Live"
default_radius_size = getenv('DEFAULT_RADIUS')
default_radius_uom = getenv('DEFAULT_RADIUS_UOM')
default_duration = getenv('DEFAULT_DURATION')
default_duration_uom = getenv('DEFAULT_DURATION_UOM')
url = "https://www.youtube.com/watch?v=CmomQkOau7c"
message = None

#working values
radius_size = getenv('DEFAULT_RADIUS')
radius_uom = getenv('DEFAULT_RADIUS_UOM')
duration = getenv('DEFAULT_DURATION')
duration_uom = getenv('DEFAULT_DURATION_UOM')

#models
prototxtPath = os.path.sep.join(["./model/face_detector", "deploy.prototxt"])
weightsPath = os.path.sep.join(["./model/face_detector", "res10_300x300_ssd_iter_140000.caffemodel"])
maskNet = load_model("./model/mask_detect.model")
faceNet = cv2.dnn.readNet(prototxtPath, weightsPath)

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    global active, url, paused, linkedstream
    if request.form.get("source_path") != None:
        active = "Link"
        url = request.form.get("source_path")
        linkedstream = LinkedStream(url).start()
    elif request.form.get("live_feed") != None:
        if linkedstream is not None:
            linkedstream.stop()
        active = "Live"
    elif request.form.get("Reset") != None:
        reset()
    elif request.form.get("radius") != None:
        set_radius(request.form.get("radius"))
        set_duration(request.form.get("duration"))
    elif request.form.get("pause") != None:
        linkedstream.pause()
    elif request.form.get("replay") != None:
        linkedstream.stop()
        linkedstream = LinkedStream(url).start()
    else:
        active = "Live"
    return render_template("index.html", 
      active=active, message=message, url=url, radius=get_radius(), duration=get_duration())

def reset():
    global radius_size, radius_uom, duration, duration_uom, message
    radius_size = default_radius_size
    radius_uom  = default_radius_uom
    duration = default_duration
    duration_uom  = default_duration_uom
    message = None

def set_radius(radius):
    global radius_size, radius_uom, message
    parts = radius.split()
    radius_size = parts[0]
    radius_uom = parts[1]
    message = "User submitted radius of {} {} and duration of {} {}." \
    .format(radius_size, radius_uom, duration, duration_uom)

def get_radius():
    return "{} {}".format(radius_size, radius_uom)

def set_duration(new_duration):
    global duration, duration_uom, message
    parts = new_duration.split()
    duration = parts[0]
    duration_uom = parts[1]
    message = "User submitted radius of {} {} and duration of {} {}." \
    .format(radius_size, radius_uom, duration, duration_uom)

def get_duration():
    return "{} {}".format(duration, duration_uom)

def detect_motion():
    global livestream, linkedstream, outframe, lock
    while True:
        if active == "Live":
            frame = livestream.read()
        else:
            frame = linkedstream.read()

        if frame is None:
            continue

        (h, w) = frame.shape[:2]
        scale = 400/float(w)
        frame = cv2.resize(frame, (400, int(h*scale)), interpolation=cv2.INTER_AREA)

        with lock:
            outframe = frame.copy()

def process_faces(face_locations, predictions, frame):
    for location, pred in zip(face_locations, predictions):
        top, right, bottom, left = location
        (mask, naked) = pred

        label = "Mask" if mask > withoutMask else "No Mask"
        color = (0, 255, 0) if label == "Mask" else (0, 0, 255)

        # include the probability in the label
        label = "{}: {:.2f}%".format(label, max(mask, withoutMask) * 100)

        cv2.putText(frame, label, (startX, startY - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 2)
        cv2.rectangle(frame, (startX, startY), (endX, endY), color, 2)
    return frame

def detect_and_predict_mask(frame, faceNet, maskNet):
    (h, w) = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(frame, 1.0, (300, 300), (104.0, 177.0, 123.0))
    faceNet.setInput(blob)
    detections = faceNet.forward()
    faces = []
    locs = []
    preds = []
    for i in range(0, detections.shape[2]):
        confidence = detections[0, 0, i, 2]
        (startX, startY, endX, endY) = (detections[0, 0, i, 3:7] * np.array([w, h, w, h])).astype("int")
        (startX, startY) = (max(0, startX), max(0, startY))
        (endX, endY) = (min(w - 1, endX), min(h - 1, endY))
        face = frame[startY:endY, startX:endX]
        face = cv2.cvtColor(face, cv2.COLOR_BGR2RGB)
        face = cv2.resize(face, (224, 224))
        face = img_to_array(face)
        face = preprocess_input(face)
        faces.append(face)
        locs.append((startX, startY, endX, endY))
    if len(faces) > 0:
        faces = np.array(faces, dtype="float32")
        preds = maskNet.predict(faces, batch_size=32)
    return (locs, preds)

def generate():
    global outframe, lock
    while True:
        with lock:
            if outframe is None:
                continue
            (flag, encodedImage) = cv2.imencode(".jpg", outframe)
            if not flag:
                continue
        yield(b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + bytearray(encodedImage) + b'\r\n')

@app.route("/video_feed")
def video_feed():
    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")

if __name__ == "__main__":
    t = threading.Thread(target=detect_motion)
    t.daemon = True
    t.start()
    app.run(debug=True, host="0.0.0.0", port=8080, threaded=True, use_reloader=False)

livestream.stop()
