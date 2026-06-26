import eventlet
eventlet.monkey_patch()

import os
import time
import json
import math
import threading

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

# — Last-known GPS location (shared across all clients) —
_last_location = None
_last_location_lock = threading.Lock()

# — MQTT setup —
MQTT_BROKER = os.getenv('MQTT_BROKER', 'broker.hivemq.com')
MQTT_PORT   = int(os.getenv('MQTT_PORT', '1883'))
TOPIC_INPUT    = 'myrc/car1/input'
TOPIC_RESTART  = 'myrc/car1/restart'
mqttc = mqtt.Client()
# connect_async + loop_start: the server still starts if the broker isn't up
# yet and auto-reconnects — important when running a local broker.
mqttc.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
mqttc.loop_start()
print(f"📡 MQTT broker: {MQTT_BROKER}:{MQTT_PORT} (set MQTT_BROKER env var to change)")

# — Gear & smoothing (time-based, frame-rate independent) —
GEAR_SCALE      = [0.0, 0.10, 0.3, 0.5, 0.7, 1.0]
# Smoothing time constants (seconds). Smaller = snappier response.
TAU_STEER       = 0.05   # steering: near-instant
TAU_THROTTLE    = 0.12   # throttle: short ramp to spare the drivetrain
TAU_GEAR        = 0.15   # gear-scale change
MAX_DT          = 0.25   # clamp elapsed time so a stall can't jump the output
steer_smooth    = 0.0
throttle_smooth = 0.0
scale_smooth    = GEAR_SCALE[1]
_last_input_t   = None

# — Camera URL (configurable via env, no crash if unavailable) —
CAMERA_URL = os.getenv('CAMERA_URL', 'http://10.212.49.6:8080/video')

print(f"📷 Camera configured: {CAMERA_URL}")
print(f"   To change: set environment variable CAMERA_URL, or modify app.py default")
print(f"   ℹ️  Camera connects on demand (when /video_feed is requested)")

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

@app.route('/dashboard')
def dashboard():
    return render_template('Dashboard.html')

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
    # Send last-known GPS location immediately so the map shows something
    with _last_location_lock:
        if _last_location is not None:
            socketio.emit('location', _last_location)
            print(f"[{time.strftime('%H:%M:%S')}]   ↳ Sent last location to new client")

    # camera status is determined when /video_feed is requested
    socketio.emit('camera_status', {'online': 'checking'})

@socketio.on('location')
def handle_location(data):
    # Persist the location server-side
    with _last_location_lock:
        _last_location = {
            'lat': data.get('lat'),
            'lng': data.get('lng'),
            'accuracy': data.get('accuracy', 0),
            'altitude': data.get('altitude', 0),
            'speed': data.get('speed', 0),
            'heading': data.get('heading', 0)
        }
    print(f"[{time.strftime('%H:%M:%S')}] 📍 location: lat={_last_location['lat']:.6f}, "
          f"lng={_last_location['lng']:.6f}, acc={_last_location['accuracy']}m")
    # Broadcast to all connected dashboards
    socketio.emit('location', _last_location)


@socketio.on('controller_input')
def handle_input(data):
    global steer_smooth, throttle_smooth, scale_smooth, _last_input_t

    raw_steer = data.get('steering', 0.0)
    raw_thr   = data.get('throttle', 0.0)
    raw_brk   = data.get('brake', 0.0)
    gear      = int(data.get('gear', 1))
    gear      = max(0, min(gear, len(GEAR_SCALE) - 1))  # guard against bad index

    # Time-based smoothing: alpha depends on elapsed time, not packet rate, so
    # the feel stays consistent even when packets are dropped or arrive bursty.
    now = time.time()
    dt = MAX_DT if _last_input_t is None else min(now - _last_input_t, MAX_DT)
    _last_input_t = now
    a_steer = 1.0 - math.exp(-dt / TAU_STEER)
    a_thr   = 1.0 - math.exp(-dt / TAU_THROTTLE)
    a_gear  = 1.0 - math.exp(-dt / TAU_GEAR)

    steer_smooth += a_steer * (raw_steer - steer_smooth)
    angle = int((steer_smooth * 0.5 + 0.5) * 180)

    forward = -raw_brk if raw_brk > 0.1 else raw_thr
    throttle_smooth += a_thr * (forward - throttle_smooth)

    target_scale = GEAR_SCALE[gear]
    scale_smooth += a_gear * (target_scale - scale_smooth)

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
    port = int(os.environ.get('PORT', 5002))
    print(f"Starting server on http://0.0.0.0:{port}/")
    socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False)
