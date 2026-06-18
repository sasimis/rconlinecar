#include <WiFi.h>
#include <PubSubClient.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>

// ===== CONFIGURATION =====
const char* SSID       = "Redmi10s";
const char* PASS       = "12345678";
const char* MQTT_BROKER = "broker.hivemq.com";
const int   MQTT_PORT   = 1883;
const char* TOPIC_INPUT    = "myrc/car1/input";
const char* TOPIC_RESTART  = "myrc/car1/restart";

// ===== PIN ASSIGNMENTS =====
const int STEERING_PIN = 18;
const int ESC_PIN      = 19;

// ===== GLOBALS =====
WiFiClient    net;
PubSubClient  mqtt(net);
Servo steeringServo, esc;

// state
int  steeringAngle = 90;
int  throttlePWM   = 0;
bool restartPending = false;

// ===== FORWARD DECLARATIONS =====
void connectWiFi();
void connectMQTT();
void onMqttMessage(char* topic, byte* payload, unsigned int len);

// ================================================================
//                    MQTT CALLBACK
// ================================================================
void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  String msg;
  for(unsigned int i = 0; i < len; i++) msg += (char)payload[i];

  // --- Check for restart command ---
  if (strcmp(topic, TOPIC_RESTART) == 0) {
    StaticJsonDocument<128> doc;
    DeserializationError err = deserializeJson(doc, msg);
    if (!err && doc["cmd"] == "restart") {
      Serial.println("🔄 Restart command received — rebooting in 1s...");
      restartPending = true;
      return;
    }
  }

  // --- Normal drive command ---
  StaticJsonDocument<200> doc;
  DeserializationError err = deserializeJson(doc, msg);
  if (err) {
    Serial.println("❌ JSON parse failed");
    return;
  }
  steeringAngle = doc["angle"];
  throttlePWM   = doc["pwm"];
  Serial.printf("📥 MQTT → angle=%d pwm=%d\n", steeringAngle, throttlePWM);
}

// ================================================================
//                    Wi-Fi CONNECTION
// ================================================================
void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  Serial.printf("\n📡 Connecting to Wi-Fi: %s ", SSID);
  WiFi.begin(SSID, PASS);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    attempts++;
    if (attempts > 40) {  // 20 seconds timeout
      Serial.println("\n⚠️  Wi-Fi timeout — will retry in loop()");
      return;
    }
  }
  Serial.printf("\n✅ Wi-Fi up, IP=%s\n", WiFi.localIP().toString().c_str());
}

// ================================================================
//                    MQTT CONNECTION
// ================================================================
void connectMQTT() {
  if (mqtt.connected()) return;

  Serial.print("⏳ MQTT connecting...");
  if (mqtt.connect("ESP32RCCar")) {
    Serial.println("✅");
    mqtt.subscribe(TOPIC_INPUT);
    mqtt.subscribe(TOPIC_RESTART);
    Serial.printf("🔴 Subscribed to %s, %s\n", TOPIC_INPUT, TOPIC_RESTART);
  } else {
    Serial.printf(" failed (rc=%d), retrying...\n", mqtt.state());
  }
}

// ================================================================
//                    SETUP
// ================================================================
void setup() {
  Serial.begin(115200);
  delay(500);

  connectWiFi();

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  connectMQTT();

  // Attach servos/ESC
  steeringServo.setPeriodHertz(50);
  esc.setPeriodHertz(50);
  steeringServo.attach(STEERING_PIN, 500, 2500);
  esc.attach(ESC_PIN, 1000, 2000);

  // Arm ESC (standard sequence)
  esc.writeMicroseconds(2000); delay(500);
  esc.writeMicroseconds(1000); delay(500);
  esc.writeMicroseconds(1500); delay(500);

  Serial.println("🚗 Ready!");
}

// ================================================================
//                    MAIN LOOP
// ================================================================
void loop() {
  // --- Handle pending restart ---
  if (restartPending) {
    delay(1000);
    ESP.restart();
  }

  // --- Maintain Wi-Fi (auto-reconnects if dropped) ---
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
    delay(500);
    return;  // skip MQTT/drive until Wi-Fi is back
  }

  // --- Maintain MQTT (auto-reconnects if dropped) ---
  if (!mqtt.connected()) {
    connectMQTT();
    delay(500);
    return;  // skip drive until MQTT is back
  }

  // --- Process incoming MQTT messages ---
  mqtt.loop();

  // --- Drive ---
  steeringServo.write(steeringAngle);
  int us = map(throttlePWM, -255, 255, 1000, 2000);
  esc.writeMicroseconds(constrain(us, 1000, 2000));

  // --- Yield to background tasks + reduce jitter ---
  delay(20);
}
