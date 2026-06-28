import json
import os
import socket
import ssl
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import socketio


RC_SERVER = os.getenv("RC_SERVER", "https://100.70.113.90:5002")
ESP_HOST = os.getenv("ESP_HOST", "255.255.255.255")
ESP_PORT = int(os.getenv("ESP_PORT", "4210"))
STALE_COMMAND_MS = int(os.getenv("STALE_COMMAND_MS", "450"))
AUTO_UPDATE = os.getenv("AUTO_UPDATE_BRIDGE", "1") != "0"
UPDATE_URL = os.getenv("BRIDGE_UPDATE_URL", f"{RC_SERVER.rstrip('/')}/bridge/phone_bridge.py")


udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
last_log = 0.0

sio = socketio.Client(ssl_verify=False, reconnection=True)


def maybe_self_update():
    if not AUTO_UPDATE or os.getenv("BRIDGE_UPDATED_ONCE") == "1":
        return

    script_path = Path(__file__).resolve()
    try:
        context = ssl._create_unverified_context()
        with urlopen(UPDATE_URL, timeout=8, context=context) as response:
            remote = response.read()
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] bridge update skipped: {e}")
        return

    try:
        local = script_path.read_bytes()
    except OSError:
        local = b""

    if remote == local:
        print(f"[{time.strftime('%H:%M:%S')}] bridge is up to date")
        return

    backup_path = script_path.with_suffix(script_path.suffix + ".bak")
    tmp_path = script_path.with_suffix(script_path.suffix + ".new")
    tmp_path.write_bytes(remote)
    if local:
        backup_path.write_bytes(local)
    os.replace(tmp_path, script_path)
    print(f"[{time.strftime('%H:%M:%S')}] bridge updated from {UPDATE_URL}; restarting")
    os.environ["BRIDGE_UPDATED_ONCE"] = "1"
    os.execv(sys.executable, [sys.executable, str(script_path), *sys.argv[1:]])


@sio.event
def connect():
    print(f"[{time.strftime('%H:%M:%S')}] connected to {RC_SERVER}")


@sio.event
def disconnect():
    print(f"[{time.strftime('%H:%M:%S')}] disconnected")


@sio.on("drive_command")
def drive_command(data):
    global last_log
    now = time.time()
    server_time = data.get("serverTime")
    command_age_ms = None
    if isinstance(server_time, (int, float)):
        command_age_ms = max(0, int((now - server_time) * 1000))
    stale = command_age_ms is not None and command_age_ms > STALE_COMMAND_MS
    udp_data = data
    if stale:
        udp_data = {
            "angle": data.get("angle", 90),
            "pwm": 0,
            "gear": data.get("gear", 1),
            "seq": data.get("seq"),
            "session": data.get("session"),
            "serverTime": data.get("serverTime"),
            "stale": True,
        }
    payload = json.dumps(udp_data, separators=(",", ":")).encode("utf-8")
    udp.sendto(payload, (ESP_HOST, ESP_PORT))
    sio.emit("bridge_status", {
        "seq": data.get("seq"),
        "commandAgeMs": command_age_ms,
        "stale": stale,
        "espHost": ESP_HOST,
        "espPort": ESP_PORT,
        "serverTime": now,
    })
    if stale or now - last_log > 0.5:
        last_log = now
        print(
            f"[{time.strftime('%H:%M:%S')}] -> ESP {ESP_HOST}:{ESP_PORT} "
            f"angle={udp_data.get('angle')} pwm={udp_data.get('pwm')} gear={udp_data.get('gear')} "
            f"seq={udp_data.get('seq')} age={command_age_ms}ms stale={stale}"
        )


if __name__ == "__main__":
    maybe_self_update()
    print(f"Bridge: {RC_SERVER} -> UDP {ESP_HOST}:{ESP_PORT}")
    sio.connect(RC_SERVER, transports=["websocket"])
    sio.wait()
