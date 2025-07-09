import os
import time
import json
import eventlet
eventlet.monkey_patch()

from flask import render_template

from flask import Flask, render_template, Response
from flask_socketio import SocketIO
import paho.mqtt.client as mqtt
import cv2    # ← new

# — Flask / Socket.IO setup —
app = Flask(__name__)
app.config['SECRET_KEY'] = 'rc-car-secret'
socketio = SocketIO(
    app,
    async_mode='eventlet',
    cors_allowed_origins="*",
    logger=True,
    engineio_logger=True
)

@app.route('/phone')
def phone():
    # renders the phone broadcaster template
    return render_template('phone_loc.html')

# — MQTT setup (unchanged) —
MQTT_BROKER = os.getenv('MQTT_BROKER', 'broker.hivemq.com')
MQTT_PORT   = int(os.getenv('MQTT_PORT', '1883'))
TOPIC_INPUT = 'myrc/car1/input'
mqttc = mqtt.Client()
mqttc.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
mqttc.loop_start()

# — Gear & smoothing (unchanged) —
GEAR_SCALE      = [0.0, 0.10, 0.3, 0.5, 0.7, 1.0]
SMOOTH          = 0.2
steer_smooth    = 0.0
throttle_smooth = 0.0
scale_smooth    = GEAR_SCALE[1]

# — Camera capture setup —  
# 0 = default webcam. Change to RTSP/MJPEG URL for IP cams or "http://<phone>:8080/video"
camera = cv2.VideoCapture("http://100.83.193.15:8080/video") 
if not camera.isOpened():
    raise RuntimeError("Could not start camera.")

def gen_frames():
    """Yield camera frames as multipart MJPEG."""
    while True:
        success, frame = camera.read()
        if not success:
            break
        # optionally: resize, flip, overlay etc here
        ret, buffer = cv2.imencode('.jpg', frame)
        jpg = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + jpg + b'\r\n')

@app.route('/video_feed')
def video_feed():
    return Response(
        gen_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/')
def index():
    # index.html will embed /video_feed
    return render_template('index.html')


@socketio.on('connect')
def handle_connect(auth):
    print(f"[{time.strftime('%H:%M:%S')}] Dashboard connected")
    socketio.emit('telemetry', {
        'steering': 90,
        'throttle': 0,
        'gear': 1,
        'speed': 0.0
    })
@socketio.on('location')
def handle_location(data):
    # data == {'lat':…, 'lng':…}
    print(f"[{time.strftime('%H:%M:%S')}] 📍 got location:", data)
    socketio.emit('location', data)


@socketio.on('controller_input')
def handle_input(data):
    global steer_smooth, throttle_smooth, scale_smooth

    # (your existing smoothing, MQTT publish, telemetry emit…)

    raw_steer = data.get('steering', 0.0)
    raw_thr   = data.get('throttle', 0.0)
    raw_brk   = data.get('brake', 0.0)
    gear      = data.get('gear', 1)

    steer_smooth += SMOOTH * (raw_steer - steer_smooth)
    angle = int((steer_smooth * 0.5 + 0.5) * 180)

    forward = -raw_brk if raw_brk > 0.1 else raw_thr
    throttle_smooth += SMOOTH * (forward - throttle_smooth)

    target_scale = GEAR_SCALE[gear]
    scale_smooth += SMOOTH * (target_scale - scale_smooth)

    pwm = int(throttle_smooth * scale_smooth * 255)

    payload = {'angle': angle,'pwm': pwm,'gear': gear}
    mqttc.publish(TOPIC_INPUT, json.dumps(payload), qos=0)

    speed = abs(pwm) * 0.2 * gear
    socketio.emit('telemetry', {
        'steering': angle,
        'throttle': pwm,
        'gear': gear,
        'speed': round(speed,1)
    })
@socketio.on('restart_esp')
def restart_esp():
    # your code to reset ESP (e.g. serial command or MQTT)
    print("ESP restart requested")

@socketio.on('restart_hotspot')
def restart_hotspot():
    # your code to cycle the Android hotspot
    print("Hotspot restart requested")

if __name__ == '__main__':
    print("Starting server on http://0.0.0.0:5000/")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
