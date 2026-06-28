# ExpressLRS + WebRTC Architecture

This branch starts the move away from internet-first driving. The goal is to make the RC car behave like a real RC/robotics platform:

- ExpressLRS handles steering and throttle.
- WebRTC handles low-latency video.
- The web dashboard handles telemetry, GPS, recording, configuration, and emergency supervision.
- The ESP32 remains responsible for local failsafe behavior.

## Why Change

The current prototype sends driving commands through:

```text
PC controller -> Flask/Socket.IO -> Tailscale -> phone mobile data -> Termux bridge -> UDP -> ESP32
```

This works, but mobile data latency is not deterministic. A measured `160-230ms` ping to the phone is already enough to make a fast RC car feel delayed. If the path spikes, stale commands can arrive late.

Real RC systems separate the time-critical control path from video and telemetry.

## Target Architecture

```text
Steering/throttle:
RC transmitter or PC-to-ELRS TX -> ExpressLRS RF -> ELRS receiver -> ESP32/servo/ESC

Video:
Phone or camera -> WebRTC -> dashboard

Telemetry:
ESP32 + phone GPS -> dashboard over Tailscale/WebSocket/MQTT

Safety:
ESP32 local failsafe, command timeout, throttle neutral, optional hardware kill switch
```

## Control Path Options

### Option A: Standard RC Transmitter

Use an ExpressLRS transmitter and receiver exactly like a normal RC/FPV setup.

Pros:

- Best driving feel.
- Lowest complexity.
- Works even if phone, PC, dashboard, or mobile data fails.

Cons:

- Dashboard cannot directly drive unless transmitter supports trainer/link integration.
- Needs RC transmitter hardware.

### Option B: PC Generates CRSF To ExpressLRS TX Module

The PC/controller sends CRSF frames to an ExpressLRS TX module over serial. The TX module sends RF to the receiver.

Pros:

- Keeps PS4/WASD/dashboard control from the PC.
- Avoids mobile data in the control loop.
- Still uses a dedicated RC RF link.

Cons:

- More implementation work.
- Needs a TX module wired to USB/UART adapter.
- Requires correct CRSF framing and failsafe handling.

### Option C: ELRS Receiver PWM Direct To Servo/ESC

Use the ELRS receiver PWM outputs directly for steering and throttle.

Pros:

- Very simple.
- ESP32 command latency removed from driving.

Cons:

- Less programmable smoothing/modes unless receiver/transmitter handles them.
- Telemetry and custom safety logic are separate.

## Recommended First Build

Start with Option A or C:

1. Install ExpressLRS receiver on the car.
2. Feed steering and throttle from ELRS receiver to servo/ESC or through ESP32 pulse input.
3. Keep ESP32 for telemetry/status and optional failsafe layer.
4. Keep dashboard for video/map/status.
5. Replace IP Webcam/MJPEG with WebRTC after control is stable.

This avoids rebuilding every subsystem at once.

## WebRTC Video Direction

The current `/video_feed` route uses MJPEG. It is simple, but it is bandwidth-heavy and can lag.

Target video path:

```text
Phone camera -> WebRTC publisher -> dashboard WebRTC player
```

Candidates:

- Browser-based phone page using `getUserMedia()` and `RTCPeerConnection`.
- MediaMTX as a local WebRTC relay.
- aiortc if we need Python-side WebRTC experiments.

The first production-style version should prefer a known relay/server instead of hand-rolling signaling and media transport.

## Migration Phases

### Phase 1: Freeze Current Prototype

- Keep existing dashboard working.
- Keep phone bridge available for testing.
- Add link latency visibility and stale-command protection.

### Phase 2: Hardware Control Link

- Choose ELRS hardware.
- Decide receiver output mode: PWM, CRSF UART, or SBUS.
- Wire steering/throttle path.
- Confirm failsafe goes neutral when transmitter link is lost.

### Phase 3: ESP32 Integration

- If using PWM/SBUS/CRSF into ESP32, create a new firmware folder.
- Read steering/throttle from ELRS receiver.
- Drive servo/ESC locally.
- Publish telemetry/status to dashboard.
- Keep hard throttle timeout.

### Phase 4: WebRTC Camera

- Replace MJPEG camera feed.
- Add video latency indicator.
- Keep map/telemetry overlays independent from video.

### Phase 5: Dashboard As Supervisor

- Dashboard shows camera, map, telemetry, link quality, battery, GPS, recording.
- Dashboard can send configuration, reset requests, and emergency stop.
- Dashboard is not required for basic steering/throttle.

## Open Decisions

- Which ExpressLRS hardware do we use?
- Do we want a normal RC transmitter, or PC-generated CRSF to an ELRS TX module?
- Should the ESP32 receive PWM, SBUS, or CRSF from the ELRS receiver?
- Is the phone still the camera, or do we add a dedicated camera board?
- Does the dashboard need to control the car, or only supervise once ELRS is installed?

