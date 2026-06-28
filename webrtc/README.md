# WebRTC Video Work

This folder is for replacing the current MJPEG/IP Webcam feed with lower-latency WebRTC video.

Target:

```text
Phone camera or dedicated camera -> WebRTC publisher/relay -> dashboard player
```

Preferred direction:

1. Use a proven WebRTC relay/server before hand-writing media transport.
2. Keep signaling separate from vehicle control.
3. Measure video latency in the dashboard.
4. Keep the telemetry/map overlays independent from the video stream.

The current `/video_feed` MJPEG route should remain available as a fallback until the WebRTC path is stable.

