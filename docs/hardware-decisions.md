# Hardware Decisions

Use this checklist before changing firmware.

## ExpressLRS Control

Choose one path.

### Path 1: Normal RC Transmitter

Required:

- ExpressLRS-capable RC transmitter, or transmitter with ELRS module bay.
- ExpressLRS receiver.
- Receiver outputs that can drive steering/throttle directly or feed ESP32.

Best for:

- Most reliable driving.
- Real RC feel.
- Less custom software.

### Path 2: PC To ExpressLRS TX Module

Required:

- ExpressLRS TX module.
- USB-to-UART adapter or module with USB serial support.
- PC software that sends CRSF frames from PS4/WASD input.
- ExpressLRS receiver on the car.

Best for:

- Keeping PC dashboard/controller as the command source.
- Removing mobile data from the control loop.

Risk:

- More custom protocol code.

### Path 3: Receiver PWM Direct

Required:

- ELRS receiver with PWM outputs, or PWM adapter.
- Steering servo input.
- ESC throttle input.

Best for:

- Fastest first test.
- Proving the RF control link before adding software complexity.

Risk:

- Dashboard/ESP has less authority over drive modes unless wired through ESP32.

## Receiver Output Choice

PWM:

- Simple to understand.
- Easy to test with servo/ESC.
- Uses one pin per channel.

SBUS:

- One signal wire for many channels.
- Common in RC.
- Needs reliable ESP32 SBUS decoding if routed through ESP32.

CRSF:

- Native ExpressLRS protocol.
- Can include link telemetry.
- Best long-term integration, but more code.

## Recommended Order

1. Buy/use an ELRS receiver that can output PWM or CRSF.
2. First test steering/throttle with PWM.
3. Move into ESP32 only after the RF link is proven.
4. Later, switch to CRSF if we want telemetry-rich integration.

## Phone Role After Upgrade

The phone should not be in the steering/throttle path.

Keep the phone for:

- Camera.
- GPS.
- Tailscale connectivity.
- Optional telemetry bridge.

Avoid using the phone for:

- Primary throttle.
- Primary steering.
- Safety-critical failsafe.

