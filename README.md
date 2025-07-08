# 🚗 ESP32 Wi-Fi RC Car (MQTT Controlled)

This project demonstrates a fully remote-controlled RC car using an **ESP32**, a **brushless motor with ESC**, and an **Android phone** as the communication hub and camera. Commands are sent wirelessly over **MQTT**, and live video is streamed using the phone’s camera.

---

## 📦 Features

- Real-time control of an RC car over Wi-Fi
- MQTT communication between Android + ESP32
- Live IP camera feed from Android phone
- Web-based dashboard with PS4 controller support 🎮
- Gear-based speed system with smooth throttle
- Interactive hardware diagram & modular design

---

## 🧰 Hardware Used

| Component             | Description                           |
|----------------------|---------------------------------------|
| ESP32 Dev Board       | Handles MQTT, PWM, servo/ESC signals |
| RC Car (chassis)      | With brushless motor and steering    |
| ESC (BLC-40C)         | Controls throttle via PWM            |
| Servo Motor (KS-102BK)| Controls front wheel steering        |
| Li-Po Battery         | 7.4V power source                    |
| Android Phone         | Runs Flask server + streams video    |
| USB OTG Cable         | Connects phone to ESP32 via serial   |

---

## 🧠 System Architecture

```plaintext
[ PS4 Controller ] → [ Web Dashboard ] → [ Android Flask Server ]
                                        ↓
                                [ MQTT Commands ]
                                        ↓
                                [ ESP32 Microcontroller ]
                                ↙️               ↘️
                     [ Steering Servo ]     [ ESC + Motor ]


🖥️ Software Overview
Layer	Tools & Languages
Microcontroller	Arduino C++ (ESP32, WiFi, Servo, PubSubClient)
Backend	Python (Flask, Flask-SocketIO, MQTT, OpenCV)
Frontend	HTML/CSS/JS + WebSocket Dashboard UI
Camera Stream	IP Webcam Android App
Remote Access	Tailscale (Peer-to-peer VPN)
