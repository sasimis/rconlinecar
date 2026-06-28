import json
import os
import socket
import ssl
import time

import socketio


RC_SERVER = os.getenv("RC_SERVER", "https://100.70.113.90:5002")
ESP_HOST = os.getenv("ESP_HOST", "255.255.255.255")
ESP_PORT = int(os.getenv("ESP_PORT", "4210"))


udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

sio = socketio.Client(ssl_verify=False, reconnection=True)


@sio.event
def connect():
    print(f"[{time.strftime('%H:%M:%S')}] connected to {RC_SERVER}")


@sio.event
def disconnect():
    print(f"[{time.strftime('%H:%M:%S')}] disconnected")


@sio.on("drive_command")
def drive_command(data):
    payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
    udp.sendto(payload, (ESP_HOST, ESP_PORT))
    print(
        f"[{time.strftime('%H:%M:%S')}] -> ESP {ESP_HOST}:{ESP_PORT} "
        f"angle={data.get('angle')} pwm={data.get('pwm')} gear={data.get('gear')} seq={data.get('seq')}"
    )


if __name__ == "__main__":
    print(f"Bridge: {RC_SERVER} -> UDP {ESP_HOST}:{ESP_PORT}")
    sio.connect(RC_SERVER, transports=["websocket"])
    sio.wait()
