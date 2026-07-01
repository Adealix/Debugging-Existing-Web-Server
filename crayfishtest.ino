#include <WiFi.h>
#include <PubSubClient.h>

// ================= WIFI =================
const char* ssid = "Bagalwifi";
const char* password = "kawawatao";

// ================= MQTT =================
const char* mqtt_server = "10.41.119.93";
const int mqtt_port = 1883;

WiFiClient espClient;
PubSubClient client(espClient);

// ================= DEVICE =================
const char* deviceID = "CRAYFISH_NODE_01";

// ================= WIFI =================
void setup_wifi() {
  Serial.println();
  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);

  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("WiFi Connected!");
  Serial.print("IP Address: ");
  Serial.println(WiFi.localIP());
}

// ================= MQTT CALLBACK =================
void callback(char* topic, byte* payload, unsigned int length) {
  Serial.print("Message received [");
  Serial.print(topic);
  Serial.print("] ");

  for (unsigned int i = 0; i < length; i++) {
    Serial.print((char)payload[i]);
  }

  Serial.println();
}

// ================= MQTT RECONNECT =================
void reconnect() {
  while (!client.connected()) {
    Serial.print("Connecting to MQTT...");

    if (client.connect(deviceID)) {
      Serial.println("SUCCESS");
      client.subscribe("crayfish/node01/test");
      client.publish("crayfish/node01/status", "ESP32 Connected");
    } else {
      Serial.print("FAILED, rc=");
      Serial.print(client.state());
      Serial.println();
      delay(5000);
    }
  }
}

// ================= SETUP =================
void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("================================");
  Serial.println("ESP32 MQTT TEST");
  Serial.println("================================");

  setup_wifi();
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
}

// ================= LOOP =================
void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi disconnected!");
    setup_wifi();
  }

  if (!client.connected()) {
    reconnect();
  }

  client.loop();

  static unsigned long lastPublish = 0;

  if (millis() - lastPublish > 5000) {
    String payload = "{\"device\":\"";
    payload += deviceID;
    payload += "\",\"uptime_ms\":";
    payload += String(millis());
    payload += "}";

    client.publish("crayfish/node01/data", payload.c_str());

    Serial.print("Published: ");
    Serial.println(payload);

    lastPublish = millis();
  }
}