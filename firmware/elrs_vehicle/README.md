# ELRS Vehicle Firmware

This folder is for the next vehicle-side firmware after the control path moves to ExpressLRS.

Planned responsibilities:

- Read ExpressLRS receiver output by PWM, SBUS, or CRSF.
- Drive steering servo and ESC locally.
- Apply local failsafe if the RF link is lost.
- Publish telemetry to the dashboard.
- Keep throttle neutral on boot, stale input, or invalid signal.

Do not put phone/Tailscale command handling here. The phone should not be in the primary driving path after the ELRS migration.

