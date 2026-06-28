#include <WiFi.h>
#include <WiFiUdp.h>
#include <PubSubClient.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>

// ===== CONFIGURATION =====
const char* SSID       = "Redmi10s";
const char* PASS       = "12345678";
const char* MQTT_BROKER = "broker.hivemq.com";
const int   MQTT_PORT   = 1883;
const int   UDP_PORT    = 4210;
const char* TOPIC_INPUT    = "myrc/car1/input";
const char* TOPIC_RESTART  = "myrc/car1/restart";
const char* TOPIC_STATUS   = "myrc/car1/status";

// ===== PIN ASSIGNMENTS =====
const int STEERING_PIN = 18;
const int ESC_PIN      = 19;

// ===== FAILSAFE =====
// If no drive command arrives within this window, cut throttle to neutral
// (prevents runaways and reconnect-jerks when the cellular link stalls).
const unsigned long FAILSAFE_MS = 300;

// ===== GLOBALS =====
WiFiClient    net;
PubSubClient  mqtt(net);
WiFiUDP       udp;
Servo steeringServo, esc;

// state
int  steeringAngle = 90;
int  throttlePWM   = 0;
bool restartPending = false;
unsigned long lastReconnectAttempt = 0;
unsigned long lastCmdTime = 0;   // millis() of last drive command (for failsafe)
unsigned long lastStatusPublish = 0;
unsigned long lastCommandSeq = 0;
String commandSession = "";

// ===== FORWARD DECLARATIONS =====
void connectWiFi();
void connectMQTT();
void applyDriveCommand(JsonDocument& doc);
void onMqttMessage(char* topic, byte* payload, unsigned int len);

void applyDriveCommand(JsonDocument& doc) {
  const char* incomingSession = doc["session"] | "";
  unsigned long incomingSeq = doc["seq"] | 0;
  if (strlen(incomingSession) > 0 && commandSession != incomingSession) {
    commandSession = incomingSession;
    lastCommandSeq = 0;
  }
  if (incomingSeq > 0 && incomingSeq <= lastCommandSeq) {
    return;
  }
  if (incomingSeq > 0) {
    lastCommandSeq = incomingSeq;
  }
  steeringAngle = doc["angle"];
  throttlePWM   = doc["pwm"];
  lastCmdTime   = millis();
}

// ================================================================
//                    MQTT CALLBACK (OPTIMIZED)
// ================================================================
void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  // 1. Instantly parse JSON into a shared document to avoid doing it twice
  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, payload, len); // Pass payload directly, no String copy
  
  if (err) {
    return; // Silently drop bad packets to save CPU time
  }

  // 2. Isolate logic strictly by topic string comparison
  if (strcmp(topic, TOPIC_RESTART) == 0) {
    if (doc["cmd"] == "restart") {
      Serial.println("🔄 Restart command received — rebooting...");
      restartPending = true;
    }
    return;
  }

  if (strcmp(topic, TOPIC_INPUT) == 0) {
    applyDriveCommand(doc);
    // Removed Serial.printf to prevent hardware transmission lag
  }
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
    delay(100); // Reduced delay for faster initial boot check
    Serial.print(".");
    attempts++;
    if (attempts > 100) {  // 10 seconds timeout
      Serial.println("\n⚠️  Wi-Fi timeout — retrying in loop...");
      return;
    }
  }
  Serial.printf("\n✅ Wi-Fi up, IP=%s\n", WiFi.localIP().toString().c_str());
}

// ================================================================
//                    MQTT CONNECTION (NON-BLOCKING RETRY)
// ================================================================
void connectMQTT() {
  if (mqtt.connected()) return;
  
  Serial.print("⏳ MQTT connecting...");
  // Use a unique client ID to prevent broker kick-offs
  String clientId = "ESP32RCCar-" + String(random(0, 10000));
  
  if (mqtt.connect(clientId.c_str())) {
    Serial.println("✅ Connected!");
    mqtt.subscribe(TOPIC_INPUT, 0);   // Explicitly Force QoS 0 for instant processing
    mqtt.subscribe(TOPIC_RESTART, 0); // Explicitly Force QoS 0
    mqtt.publish(TOPIC_STATUS, "{\"online\":true,\"event\":\"connected\"}", false);
  } else {
    Serial.printf(" failed (rc=%d)\n", mqtt.state());
  }
}

// ================================================================
//                    SETUP
// ================================================================
void setup() {
  Serial.begin(115200);
  delay(250);

  connectWiFi();

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  connectMQTT();
  udp.begin(UDP_PORT);

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
  if (restartPending) {
    delay(500);
    ESP.restart();
  }

  // --- Non-blocking Connection Handler ---
  unsigned long now = millis();
  if (WiFi.status() != WL_CONNECTED) {
    // Try reconnecting without stopping the entire code loop execution permanently
    if (now - lastReconnectAttempt > 5000) {
      lastReconnectAttempt = now;
      connectWiFi();
    }
    return; 
  }

  if (!mqtt.connected()) {
    if (now - lastReconnectAttempt > 3000) {
      lastReconnectAttempt = now;
      connectMQTT();
    }
  }

  // --- Process incoming MQTT messages ---
  if (mqtt.connected()) {
    mqtt.loop();
  }

  int packetSize = udp.parsePacket();
  if (packetSize > 0) {
    char buffer[256];
    int len = udp.read(buffer, sizeof(buffer) - 1);
    if (len > 0) {
      buffer[len] = '\0';
      StaticJsonDocument<256> doc;
      DeserializationError err = deserializeJson(doc, buffer);
      if (!err) {
        applyDriveCommand(doc);
      }
    }
  }

  if (mqtt.connected() && now - lastStatusPublish > 1000) {
    lastStatusPublish = now;
    StaticJsonDocument<128> status;
    status["online"] = true;
    status["age_ms"] = now - lastCmdTime;
    status["wifi"] = WiFi.RSSI();
    char buffer[128];
    size_t len = serializeJson(status, buffer);
    mqtt.publish(TOPIC_STATUS, reinterpret_cast<const uint8_t*>(buffer), len, false);
  }

  // --- Failsafe: stale link -> throttle to neutral (steering holds last) ---
  int safePWM = throttlePWM;
  if (millis() - lastCmdTime > FAILSAFE_MS) {  // fresh read: callback may have run in mqtt.loop()
    safePWM = 0;
  }

  // --- Write Actuator Outputs ---
  steeringServo.write(steeringAngle);
  int us = map(safePWM, -255, 255, 1000, 2000);
  esc.writeMicroseconds(constrain(us, 1000, 2000));

  // Minimum required yield background time
  delay(10);
}
