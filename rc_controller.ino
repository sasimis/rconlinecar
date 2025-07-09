#include <WiFi.h>
#include <PubSubClient.h>
#include <ESP32Servo.h>
// ← Add this:
#include <ArduinoJson.h>   
// your home-hotspot or any Internet-connected Wi-Fi
const char* SSID     = "Redmi10s";
const char* PASS     = "12345678";

// public broker
const char* MQTT_BROKER = "broker.hivemq.com";
const int   MQTT_PORT   = 1883;
const char* TOPIC_INPUT = "myrc/car1/input";

WiFiClient    net;
PubSubClient  mqtt(net);

// pins
const int STEERING_PIN = 18;
const int ESC_PIN      = 19;

// servos
Servo steeringServo, esc;

// state
int steeringAngle = 90;
int throttlePWM   = 0;

// parse incoming MQTT JSON
void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  // copy into a String
  String msg;
  for(int i=0;i<len;i++) msg += (char)payload[i];
  // expected: {"angle":…, "pwm":…, "gear":…}
  StaticJsonDocument<200> doc;
  DeserializationError err = deserializeJson(doc, msg);
  if(err) {
    Serial.println("❌ JSON parse failed");
    return;
  }
  steeringAngle = doc["angle"];
  throttlePWM   = doc["pwm"];
  // (you can read gear too if you want)
  Serial.printf("📥 MQTT → angle=%d pwm=%d\n", steeringAngle, throttlePWM);
}

void setup() {
  Serial.begin(115200);
  WiFi.begin(SSID, PASS);
  while(WiFi.status()!=WL_CONNECTED) {
    delay(500); Serial.print(".");
  }
  Serial.println("\n✅ Wi-Fi up, IP=" + WiFi.localIP().toString());

  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(onMqttMessage);
  while(!mqtt.connected()) {
    Serial.print("⏳ MQTT connecting…");
    if(mqtt.connect("ESP32RCCar")) {
      Serial.println("✅");
    } else {
      Serial.print(" failed, rc=");
      Serial.print(mqtt.state());
      Serial.println(" retrying in 2s");
      delay(2000);
    }
  }
  mqtt.subscribe(TOPIC_INPUT);
  Serial.println("🔴 Subscribed to " + String(TOPIC_INPUT));

  // attach servos/ESC
  steeringServo.setPeriodHertz(50);
  esc.setPeriodHertz(50);
  steeringServo.attach(STEERING_PIN, 500, 2500);
  esc.attach(ESC_PIN, 1000, 2000);
  // arm ESC
  esc.writeMicroseconds(2000); delay(500);
  esc.writeMicroseconds(1000); delay(500);
  esc.writeMicroseconds(1500); delay(500);
}

void loop() {
  mqtt.loop();  // pump incoming MQTT

  // drive
  steeringServo.write(steeringAngle);
  int us = map(throttlePWM, -255, 255, 1000, 2000);
  esc.writeMicroseconds(constrain(us,1000,2000));
}
