# RC Online Car

Remote-controlled ESP32 RC car with a camera-first driving dashboard, GPS map, PS4/WASD controls, and an Android phone acting as the mobile bridge on the car.

Project page: https://sasimis.github.io/rconlinecar/index.html

## Current Architecture

```text
PC at home
  - Flask + Socket.IO dashboard
  - PS4 controller or WASD keyboard control
  - Tailscale

        |
        | Tailscale
        v

Android phone mounted on the car
  - mobile data
  - Tailscale
  - IP Webcam camera stream
  - GPSLogger or browser GPS
  - phone_bridge.py in Termux

        |
        | UDP over the phone hotspot/local car-side Wi-Fi
        v

ESP32 on the car
  - receives UDP drive commands on port 4210
  - controls steering servo and ESC
  - keeps MQTT as a fallback/status path
```

The ESP32 is not directly on Tailscale. The phone is the bridge between the Tailscale network and the ESP32 on the car-side local network.

## Main Features

- Camera-first driving cockpit at `/dashboard`
- WASD control at lowest gear from the dashboard
- PS4 controller support through `ps4controller.py`
- Smooth gear-based throttle limiting
- Command session/sequence numbers to reject stale delayed packets
- ESP failsafe that cuts throttle if commands stop
- Android phone bridge that forwards Socket.IO drive commands to ESP32 over UDP
- GPS map with live car marker and track line
- GPSLogger HTTP endpoint for background GPS updates
- IP Webcam video feed embedded in the dashboard

## Important Files

| File | Purpose |
| --- | --- |
| `app.py` | Flask/Socket.IO server, dashboard backend, GPS endpoint, drive command scaling |
| `templates/Dashboard.html` | Camera-first driving dashboard, map, telemetry, WASD control |
| `phone_bridge.py` | Android/Termux bridge from PC over Tailscale to ESP32 over UDP |
| `rc_controller/rc_controller.ino` | ESP32 firmware for UDP/MQTT command receive, servo and ESC output |
| `ps4controller.py` | PS4 controller client for PC |
| `templates/phone_loc.html` | Browser GPS sender fallback |

## PC Setup

Install Python dependencies:

```powershell
pip install flask flask-socketio python-socketio eventlet paho-mqtt opencv-python pygame
```

Run the server:

```powershell
python app.py
```

Open:

```text
https://100.70.113.90:5002/dashboard
```

Accept the self-signed certificate warning in the browser if needed.

## Android Phone Setup

The phone is mounted on the car and uses mobile data. It should run:

- Tailscale
- IP Webcam
- Termux with `phone_bridge.py`
- GPSLogger if background GPS is needed

Termux setup:

```sh
pkg update
pkg install python
pip install "python-socketio[client]"
python phone_bridge.py
```

Default bridge:

```text
https://100.70.113.90:5002 -> UDP 255.255.255.255:4210
```

If UDP broadcast does not reach the ESP32, set the ESP IP manually:

```sh
ESP_HOST=192.168.43.xxx python phone_bridge.py
```

## ESP32 Setup

Upload:

```text
rc_controller/rc_controller.ino
```

The ESP32 listens for UDP drive commands on:

```text
port 4210
```

MQTT is still present as a fallback/status path, but the primary driving path is now the phone bridge.

## Controls

Dashboard WASD:

```text
W = brake/reverse
S = forward
A = left
D = right
```

PS4 controller:

```powershell
python ps4controller.py
```

Known issue: PS4 Bluetooth pairing on the PC is unreliable right now, and the current USB cable is loose. Use a better data cable, fix Bluetooth pairing/driver, or use dashboard WASD for testing.

## GPS

GPSLogger can send background GPS to:

```text
http://100.70.113.90:5003/api/location?lat=%LAT&lon=%LON&acc=%ACC&alt=%ALT&speed=%SPD&dir=%DIR
```

Use `GET`, leave body and headers empty.

## Robustness Notes

- Keep drive commands on UDP through the phone bridge for lower latency.
- Keep the ESP failsafe enabled; it cuts throttle if commands stop.
- Use command sequence/session IDs to ignore delayed stale commands.
- Avoid public MQTT for primary driving commands.
- Keep the phone bridge visible/running in Termux while driving.
- For PS4 control, avoid loose USB cables; dropped controller input can look like network delay.

## Current Known Issues

- Camera feed depends on the IP Webcam URL being reachable from the PC over Tailscale.
- GPSLogger update rate can be slower than real-time if Android batches location updates.
- PS4 controller Bluetooth needs troubleshooting on the PC.
