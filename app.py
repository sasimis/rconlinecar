import os
import time
import json
import threading
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, Response
from flask_socketio import SocketIO
import paho.mqtt.client as mqtt
import cv2

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

# — MQTT setup —
MQTT_BROKER = os.getenv('MQTT_BROKER', 'broker.hivemq.com')
MQTT_PORT   = int(os.getenv('MQTT_PORT', '1883'))
TOPIC_INPUT    = 'myrc/car1/input'
TOPIC_RESTART  = 'myrc/car1/restart'
mqttc = mqtt.Client()
mqttc.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
mqttc.loop_start()

# — Gear & smoothing —
GEAR_SCALE      = [0.0, 0.10, 0.3, 0.5, 0.7, 1.0]
SMOOTH          = 0.2
steer_smooth    = 0.0
throttle_smooth = 0.0
scale_smooth    = GEAR_SCALE[1]

# — Camera URL (configurable via env, no crash if unavailable) —
CAMERA_URL = os.getenv('CAMERA_URL', 'http://100.83.193.15:8080/video')

# — Lock for thread-safe camera access —
_camera_lock = threading.Lock()
_camera = None

def _open_camera():
    """Open camera with retry. Returns None on failure (non-blocking)."""
    global _camera
    cap = cv2.VideoCapture(CAMERA_URL)
    if cap.isOpened():
        with _camera_lock:
            if _camera is not None:
                _camera.release()
            _camera = cap
        print(f"[{time.strftime('%H:%M:%S')}] 📷 Camera connected: {CAMERA_URL}")
        return True
    cap.release()
    print(f"[{time.strftime('%H:%M:%S')}] ⚠️  Camera unreachable: {CAMERA_URL}")
    return False

def _close_camera():
    global _camera
    with _camera_lock:
        if _camera is not None:
            _camera.release()
            _camera = None

# Attempt initial camera connection (non-fatal)
_open_camera()

def gen_frames():
    """Yield camera frames as multipart MJPEG.
    Opens its own capture per client to avoid greenlet races.
    """
    cap = cv2.VideoCapture(CAMERA_URL)
    if not cap.isOpened():
        print(f"[{time.strftime('%H:%M:%S')}] ⚠️  video_feed: camera not available")
        # yield a placeholder frame so the <img> tag doesn't break
        placeholder = _make_placeholder_frame("Camera offline")
        yield placeholder
        return

    consecutive_failures = 0
    while True:
        success, frame = cap.read()
        if not success:
            consecutive_failures += 1
            if consecutive_failures >= 10:
                print(f"[{time.strftime('%H:%M:%S')}] ❌ video_feed: camera lost, giving up")
                break
            eventlet.sleep(1)
            continue
        consecutive_failures = 0
        ret, buffer = cv2.imencode('.jpg', frame)
        jpg = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + jpg + b'\r\n')
        eventlet.sleep(0)  # yield to other greenlets

    cap.release()
    # yield a "camera offline" frame so the browser doesn't hang
    yield _make_placeholder_frame("Camera offline")

def _make_placeholder_frame(text="No Signal"):
    """Create a small placeholder JPEG so the <img> tag shows something."""
    import numpy as np
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    # draw centred text (OpenCV font)
    cv2.putText(img, text, (60, 120),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    ret, buffer = cv2.imencode('.jpg', img)
    jpg = buffer.tobytes()
    return (b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n' + jpg + b'\r\n')

@app.route('/video_feed')
def video_feed():
    return Response(
        gen_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )

@app.route('/phone')
def phone():
    return render_template('phone_loc.html')

@app.route('/')
def index():
    return render_template('index.html')


@socketio.on('connect')
def handle_connect(auth):
    print(f"[{time.strftime('%H:%M:%S')}] 🟢 Dashboard connected")
    socketio.emit('telemetry', {
        'steering': 90,
        'throttle': 0,
        'gear': 1,
        'speed': 0.0
    })
    # tell dashboard whether camera is alive
    ok = _camera is not None and _camera.isOpened()
    socketio.emit('camera_status', {'online': ok})

@socketio.on('location')
def handle_location(data):
    print(f"[{time.strftime('%H:%M:%S')}] 📍 location: {data}")
    socketio.emit('location', data)


@socketio.on('controller_input')
def handle_input(data):
    global steer_smooth, throttle_smooth, scale_smooth

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

    payload = {'angle': angle, 'pwm': pwm, 'gear': gear}
    mqttc.publish(TOPIC_INPUT, json.dumps(payload), qos=0)

    speed = abs(pwm) * 0.2 * gear
    socketio.emit('telemetry', {
        'steering': angle,
        'throttle': pwm,
        'gear': gear,
        'speed': round(speed, 1)
    })


@socketio.on('restart_esp')
def restart_esp():
    """Publish an MQTT restart command to the ESP."""
    print(f"[{time.strftime('%H:%M:%S')}] 🔄 ESP restart requested — publishing to {TOPIC_RESTART}")
    mqttc.publish(TOPIC_RESTART, json.dumps({'cmd': 'restart'}), qos=1)
    socketio.emit('restart_status', {'esp': 'restarting'})


@socketio.on('restart_hotspot')
def restart_hotspot():
    """Prompt the operator to restart the Android hotspot manually."""
    print(f"[{time.strftime('%H:%M:%S')}] 🔄 Hotspot restart requested")
    # On Android, we can't programmatically restart the hotspot from here.
    # Notify the dashboard operator.
    socketio.emit('restart_status', {'hotspot': 'manual_action_required'})


if __name__ == '__main__':
    print("Starting server on http://0.0.0.0:5000/")
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
