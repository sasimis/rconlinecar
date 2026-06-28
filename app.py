import eventlet
eventlet.monkey_patch()

import os
import time
import json
import math
import threading
from urllib.parse import parse_qs

from flask import Flask, jsonify, render_template, request, Response, send_from_directory
from flask_socketio import SocketIO
import paho.mqtt.client as mqtt
import cv2

# — Flask / Socket.IO setup —
app = Flask(__name__)
app.config['SECRET_KEY'] = 'rc-car-secret'
socketio = SocketIO(
    app,
    async_mode='threading',
    cors_allowed_origins="*",
    logger=True,
    engineio_logger=True
)

# — Last-known GPS location (shared across all clients) —
_last_location = None
_last_location_lock = threading.Lock()

# GPSLogger/mobile GPS can wander by several meters even when the car is still.
# These limits keep the map useful for driving instead of drawing every noisy fix.
GPS_MAX_ACCEPTED_ACCURACY_M = float(os.getenv('GPS_MAX_ACCEPTED_ACCURACY_M', '45'))
GPS_STATIONARY_SPEED_MPS = float(os.getenv('GPS_STATIONARY_SPEED_MPS', '0.8'))
GPS_MIN_STATIONARY_MOVE_M = float(os.getenv('GPS_MIN_STATIONARY_MOVE_M', '8'))
GPS_MAX_JUMP_SPEED_MPS = float(os.getenv('GPS_MAX_JUMP_SPEED_MPS', '22'))


def _coerce_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_float(data, *names):
    for name in names:
        value = _coerce_float(data.get(name))
        if value is not None:
            return value
    return None


def _normalize_location(data):
    data = data or {}
    lat = _first_float(data, 'lat', 'latitude')
    lng = _first_float(data, 'lng', 'lon', 'long', 'longitude')
    if lat is None or lng is None:
        return None
    return {
        'lat': lat,
        'lng': lng,
        'accuracy': _first_float(data, 'accuracy', 'acc') or 0,
        'altitude': _first_float(data, 'altitude', 'alt') or 0,
        'speed': _first_float(data, 'speed', 'spd') or 0,
        'heading': _first_float(data, 'heading', 'bearing', 'dir', 'direction') or 0,
        'serverTime': time.time()
    }


def _distance_m(a, b):
    earth_radius_m = 6371000.0
    lat1 = math.radians(a['lat'])
    lat2 = math.radians(b['lat'])
    dlat = lat2 - lat1
    dlng = math.radians(b['lng'] - a['lng'])
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    )
    return earth_radius_m * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def _filter_location(location, previous):
    accuracy = float(location.get('accuracy') or 0)
    speed = max(0.0, float(location.get('speed') or 0))

    if accuracy > GPS_MAX_ACCEPTED_ACCURACY_M:
        return None, f"accuracy {accuracy:.0f}m over limit"

    if previous is None:
        return location, None

    distance = _distance_m(previous, location)
    dt = max(0.1, location['serverTime'] - previous.get('serverTime', location['serverTime']))
    implied_speed = distance / dt
    jitter_radius = max(GPS_MIN_STATIONARY_MOVE_M, accuracy * 1.2)

    if speed <= GPS_STATIONARY_SPEED_MPS and distance <= jitter_radius:
        return None, f"stationary jitter {distance:.1f}m"

    if implied_speed > GPS_MAX_JUMP_SPEED_MPS and distance > jitter_radius:
        return None, f"jump {distance:.1f}m in {dt:.1f}s"

    return location, None

# — MQTT setup —
MQTT_BROKER = os.getenv('MQTT_BROKER', 'broker.hivemq.com')
MQTT_PORT   = int(os.getenv('MQTT_PORT', '1883'))
TOPIC_INPUT    = 'myrc/car1/input'
TOPIC_RESTART  = 'myrc/car1/restart'
TOPIC_STATUS   = 'myrc/car1/status'
mqttc = mqtt.Client()
_last_controller_input = 0
_command_seq = 0
COMMAND_SESSION = str(int(time.time() * 1000))


def _emit_esp_status(online, payload=None):
    socketio.emit('esp_status', {
        'online': online,
        'payload': payload or {},
        'serverTime': time.time()
    })


def _on_mqtt_connect(client, userdata, flags, rc, *extra):
    if rc == 0:
        client.subscribe(TOPIC_STATUS, qos=0)
        print(f"[{time.strftime('%H:%M:%S')}] MQTT subscribed to {TOPIC_STATUS}")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] MQTT connect failed rc={rc}")


def _on_mqtt_message(client, userdata, msg):
    if msg.topic != TOPIC_STATUS:
        return
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {'raw': msg.payload.decode('utf-8', errors='replace')}
    _emit_esp_status(True, payload)


mqttc.on_connect = _on_mqtt_connect
mqttc.on_message = _on_mqtt_message
# connect_async + loop_start: the server still starts if the broker isn't up
# yet and auto-reconnects — important when running a local broker.
mqttc.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
mqttc.loop_start()
print(f"📡 MQTT broker: {MQTT_BROKER}:{MQTT_PORT} (set MQTT_BROKER env var to change)")

# — Gear & smoothing (time-based, frame-rate independent) —
GEAR_SCALE      = [0.0, 0.12, 0.18, 0.26, 0.36, 0.50]
# Smoothing time constants (seconds). Smaller = snappier response.
TAU_STEER       = 0.05   # steering: near-instant
TAU_THROTTLE    = 0.35   # throttle: slow crawl ramp for precise low-speed control
TAU_GEAR        = 0.15   # gear-scale change
MAX_DT          = 0.25   # clamp elapsed time so a stall can't jump the output
steer_smooth    = 0.0
throttle_smooth = 0.0
scale_smooth    = GEAR_SCALE[1]
_last_input_t   = None

# — Camera URL (configurable via env, no crash if unavailable) —
CAMERA_URL = os.getenv('CAMERA_URL', 'http://100.111.238.108:8080/video')  # IP Webcam via Tailscale

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

@app.route('/templates/<path:filename>')
def serve_templates(filename):
    """Serve static assets from the templates folder (images, CSS, JS, etc.)"""
    return send_from_directory('templates', filename)


@app.route('/bridge/phone_bridge.py')
def serve_phone_bridge():
    """Serve the latest phone bridge script to the Android phone."""
    return send_from_directory('.', 'phone_bridge.py', mimetype='text/x-python')


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


@app.route('/api/location', methods=['GET', 'POST'])
def api_location():
    data = request.get_json(silent=True) or request.form.to_dict() or request.args.to_dict()
    if not data and request.data:
        raw_body = request.data.decode('utf-8', errors='replace')
        try:
            data = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {key: values[-1] for key, values in parse_qs(raw_body).items()}
            if not data:
                print(f"[{time.strftime('%H:%M:%S')}] unparsed GPS body: {raw_body!r}")
    result = handle_location(data)
    status = 200 if result.get('ok') else 400
    return jsonify(result), status

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
    _emit_esp_status(False, {'state': 'waiting_for_heartbeat'})

@socketio.on('location')
def handle_location(data):
    global _last_location
    location = _normalize_location(data)
    if location is None:
        print(f"[{time.strftime('%H:%M:%S')}] invalid GPS location ignored: {data}")
        return {'ok': False, 'error': 'invalid location'}

    with _last_location_lock:
        accepted, reason = _filter_location(location, _last_location)
        if accepted is None:
            print(f"[{time.strftime('%H:%M:%S')}] GPS location filtered: {reason}")
            socketio.emit('gps_filter_status', {
                'accepted': False,
                'reason': reason,
                'accuracy': location['accuracy'],
                'speed': location['speed'],
                'serverTime': location['serverTime']
            })
            return {'ok': True, 'filtered': True, 'reason': reason}
        _last_location = accepted

    print(f"[{time.strftime('%H:%M:%S')}] GPS location: lat={location['lat']:.6f}, "
          f"lng={location['lng']:.6f}, acc={location['accuracy']}m, speed={location['speed']}m/s")
    socketio.emit('location', location)
    return {'ok': True, 'location': location}

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
    global steer_smooth, throttle_smooth, scale_smooth, _last_input_t, _last_controller_input, _command_seq
    _last_controller_input = time.time()

    raw_steer = -data.get('steering', 0.0)
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

    _command_seq = (_command_seq + 1) % 1000000
    payload = {
        'angle': angle,
        'pwm': pwm,
        'gear': gear,
        'seq': _command_seq,
        'session': COMMAND_SESSION,
        'serverTime': time.time()
    }
    mqttc.publish(TOPIC_INPUT, json.dumps(payload), qos=0)
    socketio.emit('drive_command', payload)

    speed = abs(pwm) * 0.2 * gear
    socketio.emit('telemetry', {
        'steering': angle,
        'throttle': pwm,
        'gear': gear,
        'speed': round(speed, 1),
        'seq': _command_seq,
        'controllerOnline': True
    })


@socketio.on('bridge_status')
def handle_bridge_status(data):
    socketio.emit('bridge_status', data or {})


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
    import ssl as ssl_mod

    port = int(os.environ.get('PORT', 5002))

    ssl_dir = os.path.join(os.path.dirname(__file__), 'ssl')
    certfile = os.path.join(ssl_dir, 'cert.pem')
    keyfile = os.path.join(ssl_dir, 'key.pem')
    has_ssl = os.path.exists(certfile) and os.path.exists(keyfile)

    if has_ssl:
        print(f"🔒 HTTPS + SocketIO on port {port}")
        print(f"📱 Phone GPS page: https://100.70.113.90:{port}/phone")
        print(f"📊 Dashboard:      https://localhost:{port}/dashboard")
        print(f"   (accept self-signed cert warning in both phone and PC browsers)")
        # Threading mode + SSL = SocketIO + GPS all on one port
        context = ssl_mod.SSLContext(ssl_mod.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile, keyfile)
        gps_http_port = int(os.environ.get('GPS_HTTP_PORT', port + 1))

        def run_gps_http_ingest():
            print(f"GPSLogger HTTP endpoint: http://100.70.113.90:{gps_http_port}/api/location")
            app.run(host='0.0.0.0', port=gps_http_port, debug=False, use_reloader=False)

        threading.Thread(target=run_gps_http_ingest, daemon=True).start()
        socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False,
                     ssl_context=context)
    else:
        print(f"⚠️  No SSL certs — GPS may not work on Android Chrome over HTTP")
        print(f"📱 Phone GPS page: http://100.70.113.90:{port}/phone")
        print(f"📊 Dashboard:      http://localhost:{port}/dashboard")
        socketio.run(app, host='0.0.0.0', port=port, debug=False, use_reloader=False)
