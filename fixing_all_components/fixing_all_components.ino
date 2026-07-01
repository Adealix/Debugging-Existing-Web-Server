#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"
#include <Wire.h>
#include <BH1750.h>
#include <FastLED.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <Stepper.h>
#include <HardwareSerial.h>
#include <WiFi.h>
#include <PubSubClient.h>



const char* WIFI_SSID     = "Bagalwifi";
const char* WIFI_PASSWORD = "kawawatao";
const char* MQTT_BROKER   = "10.204.232.93";
const int   MQTT_PORT     = 1883;
const char* MQTT_CLIENT   = "esp32_crayfish";




// Sensor publish topics
#define TOPIC_PH              "crayfish/ph"
#define TOPIC_TURBIDITY       "crayfish/turbidity"
#define TOPIC_DISTANCE        "crayfish/distance"
#define TOPIC_LUX             "crayfish/lux"
#define TOPIC_TEMP            "crayfish/temperature"
#define TOPIC_ALERT           "crayfish/alert"
#define TOPIC_SENSORS_BATCH   "crayfish/sensors/batch"




// Status publish topics
#define TOPIC_STATUS_AIRPUMP  "crayfish/status/airpump"
#define TOPIC_STATUS_PUMP     "crayfish/status/pump"
#define TOPIC_STATUS_COOLING  "crayfish/status/cooling"
#define TOPIC_STATUS_LED      "crayfish/status/led"
#define TOPIC_STATUS_FEEDER   "crayfish/status/feeder"




// Command subscribe topics (Pi -> ESP32)
#define TOPIC_CMD_AIRPUMP         "crayfish/cmd/airpump"
#define TOPIC_CMD_PUMP            "crayfish/cmd/pump"
#define TOPIC_CMD_COOLING         "crayfish/cmd/cooling"
#define TOPIC_CMD_LED             "crayfish/cmd/led"
#define TOPIC_CMD_LED_BRIGHTNESS  "crayfish/cmd/led_brightness"
#define TOPIC_CMD_FEEDER          "crayfish/cmd/feeder"
// Payload "FEED"          -> manual feed request (only honored when ctrlFeeder.mode == MANUAL)
// Payload "FEED_DETECTED" -> Raspberry Pi camera detection (Roboflow) sees a
//                            crayfish; triggers a feed regardless of mode.
//                            Published by detection.py over MQTT, NOT serial.




// Mode subscribe topics (Pi -> ESP32)
#define TOPIC_MODE_AIRPUMP    "crayfish/mode/airpump"
#define TOPIC_MODE_PUMP       "crayfish/mode/pump"
#define TOPIC_MODE_COOLING    "crayfish/mode/cooling"
#define TOPIC_MODE_LED        "crayfish/mode/led"
#define TOPIC_MODE_FEEDER     "crayfish/mode/feeder"




WiFiClient   wifiClient;
PubSubClient mqtt(wifiClient);




// ================================
// PIN DEFINITIONS
// ================================
#define PH_PIN          34
#define AIR_PUMP        26
#define TURBIDITY_PIN   35
#define PUMP_RELAY_PIN  27
#define TRIG_PIN        5
#define ECHO_PIN        18
#define LED_PIN         23
#define NUM_LEDS        30
#define ONE_WIRE_BUS    4
#define COOLING_RELAY   25
#define I2C_SDA         21
#define I2C_SCL         22
#define SIM800_RX       16
#define SIM800_TX       17




// ================================
// THRESHOLDS & CONSTANTS
// ================================
#define PH_SAFE_LOW          6.5
#define PH_SAFE_HIGH         7.5
#define VOLTAGE_AT_7         2.56
#define SLOPE               -1.5
#define TURBIDITY_THRESHOLD  2000
#define DETECTION_OFFSET_CM  3.0
#define COOL_ON_TEMP         30.0
#define COOL_OFF_TEMP        28.0
// JSN-SR04T constants
#define SOUND_SPEED              0.0343
#define TIMEOUT_US               30000
#define JSN_BLIND_ZONE_CM        20.0
#define JSN_MAX_RANGE_CM         450.0
#define JSN_STABILITY_THRESHOLD  10.0

// JSN-SR04T: how many readings per cycle and gap between them.
// 5 readings × 500ms = ~2.5s per cycle.  3 readings × 500ms = ~1.5s.
// If readings are stable at 3, you can also try reducing JSN_GAP_MS
// to 300 (tested 150ms failed on this hardware).
#define JSN_READINGS             3
#define JSN_GAP_MS               500

// pH sensor: number of ADC samples per reading (20 = ~200ms, 15 = ~150ms)
#define PH_SAMPLES               15




// LED / Lux thresholds
// LED turns ON  when lux drops BELOW LUX_LED_ON.
// LED turns OFF when lux rises ABOVE LUX_LED_OFF.
// The 100 lx gap is a hysteresis band that prevents rapid toggling
// when ambient light hovers at the boundary.
#define LUX_LED_ON   500.0   // lux -- turn all LEDs ON  when BELOW this
#define LUX_LED_OFF  600.0   // lux -- turn all LEDs OFF when ABOVE this

// Static brightness for the strip when ON (0-255).
#define LED_BRIGHTNESS  200

// Target colour when the strip is ON -- plain white, applied identically
// to all 30 pixels.
#define LED_COLOR_R  255
#define LED_COLOR_G  255
#define LED_COLOR_B  255

// --------------------------------------------------------------------
// IMPORTANT HARDWARE NOTE on WS2812B "some LEDs stay on / wrong colour"
// --------------------------------------------------------------------
// Calling FastLED.show() twice with a delay() in between (as the old
// code did) does NOT fix inconsistent pixels -- it just displays the
// same buffer twice. If individual LEDs are stuck on, stuck off, or
// show the wrong colour, the real causes are almost always:
//   1. No ~300-500 ohm resistor in series on the data line (LED_PIN).
//   2. No large (~1000uF) capacitor across the 5V/GND at the strip's
//      input end.
//   3. The 5V rail sagging because the air pump / water pump / cooling
//      relay / stepper motor share the same supply as the LED strip.
//      Give the LED strip its own clean 5V feed, common-grounded with
//      the ESP32.
//   4. Long/noisy wiring between the ESP32 and the first pixel.
// The firmware-side fix (done below) is to build the COMPLETE target
// frame in the `leds[]` array first, then call FastLED.show() exactly
// ONCE per state change, so there is never a partially-written frame
// on the wire. If pixels are still inconsistent after this change,
// it is a wiring/power issue, not a code issue.
// --------------------------------------------------------------------




// ================================
// OBJECTS
// ================================
BH1750            lightMeter;
OneWire           oneWire(ONE_WIRE_BUS);
DallasTemperature tempSensor(&oneWire);
HardwareSerial    sim800(2);
CRGB              leds[NUM_LEDS];


const int IN1 = 13;
const int IN2 = 19;
const int IN3 = 14;
const int IN4 = 32;


Stepper feeder(2048, IN1, IN2, IN3, IN4);

// --------------------------------------------------------------------
// FEEDER DIRECTION
// --------------------------------------------------------------------
// Stepper::step(n) turns one physical direction for n > 0 and the
// opposite physical direction for n < 0. Which sign corresponds to
// "counter-clockwise" depends on how IN1..IN4 are wired to your motor
// coils, so it can only be fixed by testing on the actual hardware.
//
// FEED_STEPS is the single source of truth for every feed action in
// this sketch (manual command, camera-detected command, and the AUTO
// serial trigger). It is set NEGATIVE here as the assumed
// counter-clockwise direction.
//
// >>> If the motor actually turns clockwise with this sign, flip the
//     sign of FEED_STEPS below (change -- to no sign, or vice versa)
//     -- nothing else in the sketch needs to change. <<<
#define FEED_REVOLUTIONS  2
#define FEED_STEPS        (-2048 * FEED_REVOLUTIONS)   // negative = counter-clockwise

// A live camera typically keeps publishing "crayfish detected" on every
// frame for as long as the crayfish stays in view, not just once. Without
// a cooldown, that would call the stepper repeatedly back-to-back.
// FEED_DETECT_COOLDOWN_MS is the minimum time between two camera-triggered
// feeds; detections arriving before the cooldown expires are logged and
// ignored, not queued.
#define FEED_DETECT_COOLDOWN_MS  60000UL   // 60 s between camera-triggered feeds




// ================================
// CONTROL MODE STRUCT
// ================================
enum Mode { AUTO, MANUAL };




struct Actuator {
  Mode mode        = AUTO;
  bool manualState = false;
};




Actuator ctrlAirPump;
Actuator ctrlPump;
Actuator ctrlCooling;
Actuator ctrlLED;
Actuator ctrlFeeder;




// ================================
// RUNTIME STATE
// ================================
bool  airPumpOn        = false;
bool  cooling          = false;
bool  smsSent          = false;
float baselineDistance = 0.0;
bool  ledCurrentlyOn   = false;
bool  lastPumpState    = false;   // tracks turbidity water-pump on/off
unsigned long lastDetectFeedMs = 0;  // millis() of last camera-triggered feed (for cooldown)

// SMS non-blocking state machine  (see processSMS() below)
enum SmsStep {
  SMS_IDLE,
  SMS_SEND_AT, SMS_WAIT_AT,
  SMS_SEND_CSQ, SMS_WAIT_CSQ,
  SMS_SEND_CREG, SMS_WAIT_CREG,
  SMS_SEND_CMGF, SMS_WAIT_CMGF,
  SMS_SEND_CMGS, SMS_WAIT_CMGS_PROMPT,
  SMS_SEND_BODY, SMS_WAIT_CONFIRM,
  SMS_DONE, SMS_FAILED
};
SmsStep       smsStep      = SMS_IDLE;
String        smsMsgBuffer = "";     // the message to send
String        sim800Buf    = "";     // accumulated SIM800 response
unsigned long smsCmdSentAt = 0;      // millis() when current cmd was sent
int           smsRetries   = 0;      // retry count for current step




// ================================
// LED HELPERS
// ================================

// Build the FULL target frame (every one of the 30 pixels set to the
// exact same value) in the array, then push it with a SINGLE
// FastLED.show() call. A single atomic push is what guarantees every
// pixel ends up consistent -- repeated shows of the same buffer never
// fixes a corrupted frame, it only re-displays it.

// Force every LED to absolute black -- one consistent push.
void ledAllOff() {
  fill_solid(leds, NUM_LEDS, CRGB::Black);
  FastLED.show();
}

// Set every LED to the exact same solid white colour -- one consistent push.
// Brightness is dynamically adjustable via MQTT (ledBrightness global).
void ledAllOn() {
  int b = constrain(ledBrightness, 0, 255);
  FastLED.setBrightness(b);
  fill_solid(leds, NUM_LEDS, CRGB(LED_COLOR_R, LED_COLOR_G, LED_COLOR_B));
  FastLED.show();
}

// Central LED apply function -- used by both AUTO and MANUAL paths.
// Only publishes MQTT status when the on/off state actually changes.
void applyLED(bool turnOn) {
  bool changed = (turnOn != ledCurrentlyOn);

  if (turnOn) {
    ledAllOn();
    if (changed) {
      String s = String("ON|") + (ctrlLED.mode == AUTO ? "AUTO" : "MANUAL") + "|" + ledBrightness;
      mqtt.publish(TOPIC_STATUS_LED, s.c_str());
      Serial.print("[ACTUATOR] LED Strip: ON  | Mode: ");
      Serial.print(ctrlLED.mode == AUTO ? "AUTO" : "MANUAL");
      Serial.print(" | Brightness: ");
      Serial.println(ledBrightness);
    }
  } else {
    ledAllOff();
    if (changed) {
      String s = String("OFF|") + (ctrlLED.mode == AUTO ? "AUTO" : "MANUAL") + "|" + ledBrightness;
      mqtt.publish(TOPIC_STATUS_LED, s.c_str());
      Serial.print("[ACTUATOR] LED Strip: OFF | Mode: ");
      Serial.print(ctrlLED.mode == AUTO ? "AUTO" : "MANUAL");
      Serial.print(" | Brightness: ");
      Serial.println(ledBrightness);
    }
  }

  ledCurrentlyOn = turnOn;
}




// ================================
// HELPERS: set actuator + report status
// ================================
void setAirPump(bool on) {
  airPumpOn = on;
  digitalWrite(AIR_PUMP, on ? LOW : HIGH);
  String s = String(on ? "ON" : "OFF") + "|" +
            (ctrlAirPump.mode == AUTO ? "AUTO" : "MANUAL");
  mqtt.publish(TOPIC_STATUS_AIRPUMP, s.c_str());
  Serial.print("[ACTUATOR] Air Pump: ");
  Serial.print(on ? "ON" : "OFF");
  Serial.print(" | Mode: ");
  Serial.println(ctrlAirPump.mode == AUTO ? "AUTO" : "MANUAL");
}




void setPump(bool on) {
  digitalWrite(PUMP_RELAY_PIN, on ? LOW : HIGH);
  String s = String(on ? "ON" : "OFF") + "|" +
            (ctrlPump.mode == AUTO ? "AUTO" : "MANUAL");
  mqtt.publish(TOPIC_STATUS_PUMP, s.c_str());
  Serial.print("[ACTUATOR] Water Pump: ");
  Serial.print(on ? "ON" : "OFF");
  Serial.print(" | Mode: ");
  Serial.println(ctrlPump.mode == AUTO ? "AUTO" : "MANUAL");
}




void setCooling(bool on) {
  cooling = on;
  digitalWrite(COOLING_RELAY, on ? LOW : HIGH);
  String s = String(on ? "ON" : "OFF") + "|" +
            (ctrlCooling.mode == AUTO ? "AUTO" : "MANUAL");
  mqtt.publish(TOPIC_STATUS_COOLING, s.c_str());
  Serial.print("[ACTUATOR] Cooling: ");
  Serial.print(on ? "ON" : "OFF");
  Serial.print(" | Mode: ");
  Serial.println(ctrlCooling.mode == AUTO ? "AUTO" : "MANUAL");
}

// setLED is called only from the MQTT manual-command path.
void setLED(bool on) {
  ctrlLED.manualState = on;
  applyLED(on);
}

// Runs the feeder one counter-clockwise feed cycle (see FEED_STEPS).
// `reason` is just for the status payload / serial log (e.g. "MANUAL",
// "AUTO", "CAMERA_DETECTED").
void runFeeder(const char* reason) {
  Serial.print("[ACTUATOR] Feeder: ROTATING (CCW) | Reason: ");
  Serial.println(reason);
  feeder.step(FEED_STEPS);
  String s = String("FED|") + reason;
  mqtt.publish(TOPIC_STATUS_FEEDER, s.c_str());
  Serial.println("[ACTUATOR] Feeder: DONE");
}




// ================================
// MQTT CALLBACK
// ================================
void mqttCallback(char* topic, byte* payload, unsigned int len) {
  String msg, t;
  for (unsigned int i = 0; i < len; i++) msg += (char)payload[i];
  t = String(topic);




  // --- MODE CHANGES ---
  if (t == TOPIC_MODE_AIRPUMP) {
    ctrlAirPump.mode = (msg == "MANUAL") ? MANUAL : AUTO;
    Serial.println("[MQTT] AirPump mode -> " + msg);
  }
  else if (t == TOPIC_MODE_PUMP) {
    ctrlPump.mode = (msg == "MANUAL") ? MANUAL : AUTO;
    Serial.println("[MQTT] Pump mode -> " + msg);
  }
  else if (t == TOPIC_MODE_COOLING) {
    ctrlCooling.mode = (msg == "MANUAL") ? MANUAL : AUTO;
    Serial.println("[MQTT] Cooling mode -> " + msg);
  }
  else if (t == TOPIC_MODE_LED) {
    ctrlLED.mode = (msg == "MANUAL") ? MANUAL : AUTO;
    Serial.println("[MQTT] LED mode -> " + msg);
  }
  else if (t == TOPIC_MODE_FEEDER) {
    ctrlFeeder.mode = (msg == "MANUAL") ? MANUAL : AUTO;
    Serial.println("[MQTT] Feeder mode -> " + msg);
  }




  // --- MANUAL COMMANDS ---
  else if (t == TOPIC_CMD_AIRPUMP && ctrlAirPump.mode == MANUAL) {
    ctrlAirPump.manualState = (msg == "ON");
    setAirPump(ctrlAirPump.manualState);
  }
  else if (t == TOPIC_CMD_PUMP && ctrlPump.mode == MANUAL) {
    ctrlPump.manualState = (msg == "ON");
    setPump(ctrlPump.manualState);
  }
  else if (t == TOPIC_CMD_COOLING && ctrlCooling.mode == MANUAL) {
    ctrlCooling.manualState = (msg == "ON");
    setCooling(ctrlCooling.manualState);
  }
  else if (t == TOPIC_CMD_LED && ctrlLED.mode == MANUAL) {
    setLED(msg == "ON");
  }
  else if (t == TOPIC_CMD_LED_BRIGHTNESS && ctrlLED.mode == MANUAL) {
    int b = msg.toInt();
    ledBrightness = constrain(b, 0, 255);
    Serial.print("[MQTT] LED Brightness set to ");
    Serial.println(ledBrightness);
    if (ledCurrentlyOn) {
      ledAllOn();  // re-apply with new brightness immediately
    }
  }
  else if (t == TOPIC_CMD_FEEDER) {
    if (msg == "FEED" && ctrlFeeder.mode == MANUAL) {
      Serial.println("[MQTT] CMD: Feed (Manual)");
      runFeeder("MANUAL");
    }
    else if (msg == "FEED_DETECTED") {
      // Raspberry Pi live camera detected a crayfish -> feed now,
      // regardless of AUTO/MANUAL mode, but only if the cooldown has
      // elapsed (a live camera will keep sending this while the
      // crayfish stays in frame -- we don't want to re-feed every time).
      unsigned long now = millis();
      if (now - lastDetectFeedMs >= FEED_DETECT_COOLDOWN_MS || lastDetectFeedMs == 0) {
        Serial.println("[MQTT] CMD: Feed (Camera detected crayfish)");
        runFeeder("CAMERA_DETECTED");
        mqtt.publish(TOPIC_ALERT, "CRAYFISH_DETECTED_FED");
        lastDetectFeedMs = now;
      } else {
        unsigned long remaining = FEED_DETECT_COOLDOWN_MS - (now - lastDetectFeedMs);
        Serial.print("[MQTT] Camera detection received, but feeder cooldown active (");
        Serial.print(remaining / 1000);
        Serial.println(" s remaining) -- ignored.");
      }
    }
  }
}




// ================================
// WiFi + MQTT CONNECT
// ================================
void connectWiFi() {
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("[WIFI] Connecting");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\n[WIFI] Connected: " + WiFi.localIP().toString());

  // --------------------------------------------------------------------
  // WHY THIS MATTERS FOR THE ULTRASONIC SENSOR
  // --------------------------------------------------------------------
  // By default the ESP32 WiFi driver uses "modem sleep", which power-
  // cycles the radio in short bursts to save power. Those bursts can
  // introduce scheduling jitter right around time-critical operations
  // like pulseIn() on ECHO_PIN. Disabling modem sleep removes that
  // entire class of jitter. There is no real downside on a mains-
  // powered project like this one.
  // --------------------------------------------------------------------
  WiFi.setSleep(false);
}




void connectMQTT() {
  mqtt.setServer(MQTT_BROKER, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);

  Serial.print("[MQTT] Connecting...");
  if (mqtt.connect(MQTT_CLIENT)) {
    Serial.println("OK");
    mqtt.subscribe(TOPIC_CMD_AIRPUMP);
    mqtt.subscribe(TOPIC_CMD_PUMP);
    mqtt.subscribe(TOPIC_CMD_COOLING);
    mqtt.subscribe(TOPIC_CMD_LED);
    mqtt.subscribe(TOPIC_CMD_LED_BRIGHTNESS);
    mqtt.subscribe(TOPIC_CMD_FEEDER);
    mqtt.subscribe(TOPIC_MODE_AIRPUMP);
    mqtt.subscribe(TOPIC_MODE_PUMP);
    mqtt.subscribe(TOPIC_MODE_COOLING);
    mqtt.subscribe(TOPIC_MODE_LED);
    mqtt.subscribe(TOPIC_MODE_FEEDER);
  } else {
    Serial.print("[MQTT] Failed rc=");
    Serial.println(mqtt.state());
    Serial.println("[MQTT] Skipping MQTT -- running in offline test mode.");
  }
}




void mqttPublish(const char* topic, float value) {
  if (mqtt.connected()) mqtt.publish(topic, String(value, 2).c_str());
}
void mqttPublish(const char* topic, const char* msg) {
  if (mqtt.connected()) mqtt.publish(topic, msg);
}




// ================================
// SENSOR HELPERS
// ================================
float getAvgPHRaw() {
  int buf[PH_SAMPLES];
  for (int i = 0; i < PH_SAMPLES; i++) {
    buf[i] = analogRead(PH_PIN);
    mqtt.loop();
    delay(10);
  }
  // Partial sort: just find the middle values, skip full bubble sort
  for (int i = 0; i < PH_SAMPLES - 1; i++)
    for (int j = i + 1; j < PH_SAMPLES; j++)
      if (buf[i] > buf[j]) { int t = buf[i]; buf[i] = buf[j]; buf[j] = t; }
  // Take middle 60% (skip 20% low, 20% high)
  int skip = PH_SAMPLES / 5;
  long sum = 0;
  for (int i = skip; i < PH_SAMPLES - skip; i++) sum += buf[i];
  return sum / (float)(PH_SAMPLES - 2 * skip);
}




// ================================
// JSN-SR04T: Single distance reading
// ================================
float jsnReadOnce() {
  for (int retry = 0; retry < 3; retry++) {
    digitalWrite(TRIG_PIN, LOW);
    delayMicroseconds(5);
    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(30);
    digitalWrite(TRIG_PIN, LOW);

    unsigned long start = micros();
    while (digitalRead(ECHO_PIN) == LOW && micros() - start <= TIMEOUT_US) yield();
    if (micros() - start > TIMEOUT_US) { delay(10); continue; }

    start = micros();
    while (digitalRead(ECHO_PIN) == HIGH && micros() - start <= TIMEOUT_US) yield();
    unsigned long duration = micros() - start;

    if (duration == 0 || duration > TIMEOUT_US) { delay(10); continue; }

    return (duration * SOUND_SPEED) / 2.0;
  }

  return -1.0;
}




// ================================
// JSN-SR04T: Averaged distance with diagnostics
// ================================
// NOTE: per-reading "Reading N: ..." lines have been removed from the
// Serial Monitor on request -- only the summary (Average / Variation /
// Stability / blind-zone warnings) is printed now. Set `verbose` to
// false entirely if you want even that summary silenced.
//
// TIMING: this uses the EXACT same pattern as calibrateBaseline() --
// a millis()-based 500ms gap between triggers, with mqtt.loop() called
// while waiting -- instead of a tight delay()-based loop.
//
// SETTLE DELAY: calibration works 10/10 because it runs once on
// an otherwise-idle WiFi/RTOS stack right after connectMQTT(). In
// loop(), this function runs first and a short settle delay after
// the preceding iteration's MQTT activity helps pulseIn() stay
// reliable — the ESP32 RTOS scheduler can jitter the ECHO edge
// capture if it fires right after a busy period.
float getDistanceCM(bool verbose = false) {
  mqtt.loop();
  delay(20);
  yield();

  float sum    = 0;
  int   valid  = 0;
  float minVal = 9999;
  float maxVal = 0;

  for (int i = 0; i < JSN_READINGS; i++) {
    float d = jsnReadOnce();

    if (d >= 0) {
      sum += d;
      valid++;
      if (d < minVal) minVal = d;
      if (d > maxVal) maxVal = d;
    }

    mqtt.loop();
    // Use millis()-based gap so MQTT stays responsive during wait
    unsigned long gapStart = millis();
    while (millis() - gapStart < JSN_GAP_MS) {
      mqtt.loop();
      delay(5);
    }
  }

  if (valid < JSN_READINGS / 2 + 1) {
    if (verbose) {
      Serial.print("[JSN] DIAGNOSIS: Only ");
      Serial.print(valid);
      Serial.print("/");
      Serial.print(JSN_READINGS);
      Serial.println(" readings valid.");
    }
    return 999.0;
  }

  float average = sum / valid;

  if (verbose) {
    Serial.print("[JSN] Average: ");
    Serial.print(average);
    Serial.print(" cm (");
    Serial.print(valid);
    Serial.print("/");
    Serial.print(JSN_READINGS);
    Serial.println(" valid readings)");

    float variation = (valid > 1) ? (maxVal - minVal) : 0.0;
    if (variation > JSN_STABILITY_THRESHOLD) {
      Serial.println("[JSN] WARNING: Readings are UNSTABLE.");
    } else {
      Serial.println("[JSN] Readings are stable.");
    }

    if (average < JSN_BLIND_ZONE_CM) {
      Serial.println("[JSN] WARNING: Inside blind zone (<20 cm).");
    } else if (average > JSN_MAX_RANGE_CM) {
      Serial.println("[JSN] WARNING: Near maximum range (>450 cm).");
    }
  }

  return average;
}




// ================================
// JSN-SR04T: Baseline calibration
// ================================
float calibrateBaseline() {
  Serial.println();
  Serial.println("=================================");
  Serial.println("[JSN] BASELINE CALIBRATION START");
  Serial.println("[JSN] Remove objects within 50 cm.");
  Serial.println("[JSN] Taking 10 readings over ~5 s...");
  Serial.println("=================================");

  float total  = 0;
  int   count  = 0;
  float minVal = 9999;
  float maxVal = 0;

  unsigned long lastReading = millis();

  while (count < 10) {
    mqtt.loop();
    if (millis() - lastReading >= 500) {
      float d = jsnReadOnce();

      Serial.print("[JSN] Baseline ");
      Serial.print(count + 1);
      Serial.print("/10: ");

      if (d < 0) {
        Serial.println("NO ECHO -- skipped");
      } else {
        Serial.print(d);
        Serial.println(" cm");
        total += d;
        if (d < minVal) minVal = d;
        if (d > maxVal) maxVal = d;
        count++;
      }

      lastReading = millis();
    }
  }

  float baseline = total / 10.0;

  Serial.println();
  Serial.println("===== CALIBRATION COMPLETE =====");
  Serial.print("[JSN] Baseline Distance: ");
  Serial.print(baseline);
  Serial.println(" cm");

  float variation = maxVal - minVal;
  Serial.print("[JSN] Calibration Variation: ");
  Serial.print(variation);
  Serial.println(" cm");

  if (variation > JSN_STABILITY_THRESHOLD) {
    Serial.println("[JSN] WARNING: Sensor unstable during calibration!");
  } else {
    Serial.println("[JSN] Sensor appears stable.");
  }

  Serial.println("=================================");
  Serial.println();

  return baseline;
}




// ================================
// SMS
// ================================
// --------------------------------------------------------------------
// SIM800L SMS -- why "SMS sent" prints but no text arrives
// --------------------------------------------------------------------
// The old version fired AT commands blind, on fixed delays, and never
// checked the module's actual response. PubSubClient/Serial prints
// "[SMS] SMS sent." unconditionally once it finishes writing bytes --
// that line only means "we finished talking to the module", NOT "the
// network accepted the message". The real send result comes back
// from the module as "+CMGS: <ref>" (success) or "ERROR"/"+CMS ERROR"
// (failure), and the old code threw that response away.
//
// Root causes that produce exactly your symptom (looks like it sent,
// nothing arrives) on a SIM800L V2 board, roughly in order of how
// often they're the culprit in practice:
//
//   1. POWER -- by far the most common cause. The SIM800L needs
//      3.7-4.2V and can spike to ~2A during a transmit burst. Powering
//      it from the ESP32's 5V/3.3V pin or a weak USB supply causes a
//      brown-out/restart exactly when it tries to transmit, so the
//      AT+CMGS sequence silently fails partway through. Use a
//      dedicated 4V/2A-capable supply (e.g. an 18650 + protection
//      circuit, or a buck converter set to ~4.0V) with large
//      capacitors (e.g. 1000uF) right at the module's power pins.
//
//   2. NETWORK REGISTRATION -- the module can be powered and respond
//      to AT commands while still NOT registered on the SIM network
//      (no signal, SIM not activated/out of load, wrong APN region,
//      etc). AT+CMGF and AT+CMGS will both still respond OK-ish to a
//      registered-looking state, but the message queues and silently
//      drops. You must check AT+CREG? (registration) and AT+CSQ
//      (signal quality) before trusting a send.
//
//   3. NO RESPONSE CHECKING -- fixed delay()s assume every step
//      worked. If the module resets (see #1) between two AT commands,
//      the following commands land on a freshly-booted module that
//      hasn't replied "AT" ready yet, so the message body and Ctrl+Z
//      (byte 26) get sent into nothing/garbage, and the module never
//      sees a complete SMS to send -- yet your code still prints "SMS
//      sent." because it doesn't know any better.
//
//   4. WRONG NUMBER FORMAT -- some SIM800L firmware / network combos
//      need AT+CSCS="GSM" set first, or are picky about the country
//      code format. Less common than #1-#3 but worth ruling out.
//
// The rewrite below does NOT fix your power supply (that's a hardware
// problem only you can fix by metering your supply under load), but
// it DOES:
//   - print every AT response so you can see exactly where it fails
//   - check signal/registration before attempting to send
//   - wait for and check for "+CMGS" / "ERROR" instead of guessing
//   - return true/false so the rest of the sketch knows if it really
//     worked, instead of always claiming success
// --------------------------------------------------------------------

// ================================
// SIM800L — non-blocking helpers
// ================================

// Drains the SIM800 serial buffer into the accumulated response string.
// Returns the full accumulated buffer so far (cleared by
// sim800ClearBuf()). NEVER blocks — safe to call every loop iteration.
String sim800ReadNB() {
  while (sim800.available()) {
    char c = sim800.read();
    sim800Buf += c;
  }
  return sim800Buf;
}

void sim800ClearBuf() {
  sim800Buf = "";
}

void sim800SendCmd(const String& cmd) {
  Serial.print("[SIM800] -> ");
  Serial.println(cmd);
  sim800.println(cmd);
}

// ================================
// SMS — non-blocking state machine
// ================================
// Instead of blocking the main loop for up to 10 seconds per SMS attempt,
// processSMS() runs one step per loop() iteration (every ~2.5s).  Each step
// sends one AT command and then returns; the response is checked on
// subsequent iterations.  The entire sequence takes several loop cycles
// but never blocks for more than ~5ms per call.

// Start the SMS sequence.  Sets smsStep to the first step so that
// processSMS() will pick it up on the next loop iteration.
void smsBegin(const String& msg) {
  smsMsgBuffer  = msg;
  smsStep       = SMS_SEND_AT;
  smsRetries    = 0;
  sim800ClearBuf();
  smsCmdSentAt  = 0;
  Serial.println("[SMS] State machine started.");
}

// Called once per loop() iteration.  Advances the SMS state machine
// one step at a time without blocking.  Sets smsSent = true when the
// sequence is done (success or failure).
void processSMS() {
  if (smsStep == SMS_IDLE || smsStep == SMS_DONE || smsStep == SMS_FAILED) return;

  String resp = sim800ReadNB();  // non-blocking drain

  // --- Timeout per step: ~3s (covers multiple loop cycles) ---
  unsigned long elapsed = millis() - smsCmdSentAt;
  if (smsCmdSentAt != 0 && elapsed > 3000) {
    smsRetries++;
    if (smsRetries >= 3) {
      Serial.print("[SMS] FAILED after 3 retries at step ");
      Serial.println((int)smsStep);
      smsStep = SMS_FAILED;
      return;
    }
    Serial.print("[SMS] Timeout at step ");
    Serial.print((int)smsStep);
    Serial.print("  --  retry ");
    Serial.println(smsRetries + 1);
    // Re-send the command (step value will re-fire on next call)
    smsCmdSentAt = 0;
  }

  switch (smsStep) {

    // --- 1. Send AT, wait for OK ---
    case SMS_SEND_AT:
      sim800SendCmd("AT");
      smsCmdSentAt = millis();
      smsStep = SMS_WAIT_AT;
      break;
    case SMS_WAIT_AT:
      if (resp.indexOf("OK") != -1) {
        Serial.println("[SMS] AT OK");
        smsStep = SMS_SEND_CSQ;
        sim800ClearBuf();
      }
      break;

    // --- 2. Send AT+CSQ, check signal ---
    case SMS_SEND_CSQ:
      sim800SendCmd("AT+CSQ");
      smsCmdSentAt = millis();
      smsStep = SMS_WAIT_CSQ;
      break;
    case SMS_WAIT_CSQ:
      if (resp.indexOf("+CSQ:") != -1) {
        int csqVal = -1;
        int idx = resp.indexOf("+CSQ:");
        if (idx != -1) csqVal = resp.substring(idx + 5).toInt();
        Serial.print("[SMS] CSQ = ");
        Serial.println(csqVal);
        if (csqVal >= 0 && csqVal < 10) {
          Serial.println("[SMS] Signal very weak, continuing anyway...");
        }
        smsStep = SMS_SEND_CREG;
        sim800ClearBuf();
      }
      break;

    // --- 3. Send AT+CREG?, check registration ---
    case SMS_SEND_CREG:
      sim800SendCmd("AT+CREG?");
      smsCmdSentAt = millis();
      smsStep = SMS_WAIT_CREG;
      break;
    case SMS_WAIT_CREG:
      if (resp.indexOf("+CREG:") != -1) {
        bool reg = (resp.indexOf(",1") != -1) || (resp.indexOf(",5") != -1);
        Serial.print("[SMS] Network registration: ");
        Serial.println(reg ? "YES" : "NO");
        smsStep = reg ? SMS_SEND_CMGF : SMS_FAILED;
        sim800ClearBuf();
      }
      break;

    // --- 4. Set text mode ---
    case SMS_SEND_CMGF:
      sim800SendCmd("AT+CMGF=1");
      smsCmdSentAt = millis();
      smsStep = SMS_WAIT_CMGF;
      break;
    case SMS_WAIT_CMGF:
      if (resp.indexOf("OK") != -1) {
        smsStep = SMS_SEND_CMGS;
        sim800ClearBuf();
      }
      break;

    // --- 5. Send AT+CMGS, wait for ">" prompt ---
    case SMS_SEND_CMGS:
      sim800SendCmd("AT+CMGS=\"+639218255596\"");
      smsCmdSentAt = millis();
      smsStep = SMS_WAIT_CMGS_PROMPT;
      break;
    case SMS_WAIT_CMGS_PROMPT:
      if (resp.indexOf(">") != -1) {
        smsStep = SMS_SEND_BODY;
        sim800ClearBuf();
      }
      break;

    // --- 6. Send message body + Ctrl+Z ---
    case SMS_SEND_BODY:
      sim800.print(smsMsgBuffer);
      delay(10);
      sim800.write(26);   // Ctrl+Z
      smsCmdSentAt = millis();
      smsStep = SMS_WAIT_CONFIRM;
      Serial.println("[SMS] Body sent, waiting for confirmation...");
      break;
    case SMS_WAIT_CONFIRM:
      if (resp.indexOf("+CMGS") != -1) {
        Serial.println("[SMS] CONFIRMED SENT by module.");
        smsStep = SMS_DONE;
      } else if (resp.indexOf("ERROR") != -1) {
        Serial.println("[SMS] Module returned ERROR after send.");
        smsStep = SMS_FAILED;
      }
      break;

    default:
      break;
  }

  if (smsStep == SMS_DONE) {
    Serial.println("[SMS] ---- Send complete ----");
    smsSent = true;
    smsStep = SMS_IDLE;
    smsMsgBuffer = "";
  } else if (smsStep == SMS_FAILED) {
    Serial.println("[SMS] ---- Send FAILED ----");
    smsSent = true;   // don't re-try every loop; one attempt per detection event
    smsStep = SMS_IDLE;
    smsMsgBuffer = "";
  }
}




// ================================
// SETUP
// ================================
void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
  Serial.begin(115200);

  Serial.println();
  Serial.println("=================================");
  Serial.println("  Crayfish Monitor -- Booting");
  Serial.println("=================================");

  Wire.begin(I2C_SDA, I2C_SCL);
  lightMeter.begin(BH1750::CONTINUOUS_HIGH_RES_MODE, 0x23, &Wire);

  // Initialise FastLED then push one single black frame so every
  // pixel powers up consistently off (no partial/garbage frame left
  // over from power-on).
  FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUM_LEDS);
  FastLED.setBrightness(LED_BRIGHTNESS);
  ledAllOff();

  tempSensor.begin();
  feeder.setSpeed(10);
  // --------------------------------------------------------------------
  // SIM800L baud rate: 115200 was causing bit-level corruption
  // --------------------------------------------------------------------
  // Symptoms seen at 115200: "AT+CREG?" echoing back as "AT+CREG?-",
  // "+" corrupting to ";", "c"/"r" corrupting to "b"/"s" (single-bit
  // flips -- those characters are one bit apart in ASCII), and blocks
  // of garbage (?????) between exchanges. That pattern is a baud
  // mismatch / marginal serial link, not a logic bug: SIM800L modules
  // autobaud-lock onto whatever rate the first AT command arrives at,
  // and 115200 is too fast to survive unshifted 3.3V-to-2.8V wiring
  // reliably. Dropping to 9600 -- the SIM800L's most universally
  // stable rate -- and giving the module a moment to settle fixes it
  // in the large majority of cases.
  sim800.begin(9600, SERIAL_8N1, SIM800_RX, SIM800_TX);
  delay(1000);  // let the module's UART settle before the first AT command

  pinMode(AIR_PUMP, OUTPUT);        digitalWrite(AIR_PUMP, HIGH);
  pinMode(PUMP_RELAY_PIN, OUTPUT);  digitalWrite(PUMP_RELAY_PIN, HIGH);
  pinMode(COOLING_RELAY, OUTPUT);   digitalWrite(COOLING_RELAY, HIGH);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);

  connectWiFi();
  connectMQTT();

  baselineDistance = calibrateBaseline();

  Serial.println("[SYSTEM] Ready.");
  Serial.println("=================================");
  Serial.println();
}




// ================================
// MAIN LOOP — blocking sensor reads
// ================================
// Timing (no fixed delays — loop runs as fast as sensors allow):
//   distance 3×500ms  = 1,500ms  (services MQTT during gaps)
//   pH 15×10ms        =   150ms  (mqtt.loop() every sample)
//   turbidity         =     1ms  (instant ADC read)
//   lux               =   120ms  (BH1750 I2C)
//   temp (DS18B20)    =   750ms  (blocking, reliable)
//   SMS state machine =     0ms  (non-blocking, no per-iteration delay)
//   all other logic   =    10ms
//   ──────────────────────────
//   TOTAL             = ~2,531ms
// --------------------------------------------------

void loop() {
  if (!mqtt.connected()) {
    connectMQTT();
  }
  mqtt.loop();
  processSMS();   // non-blocking SMS state machine — never blocks the loop

  Serial.println();
  Serial.println("========== SENSOR READINGS ==========");

  // --- 1. Distance (takes ~1.5s, but services MQTT during gaps) ---
  float distance = getDistanceCM(false);
  mqttPublish(TOPIC_DISTANCE, distance);

  Serial.print("[SENSOR] Distance  : ");
  if (distance >= 999.0) {
    Serial.println("NO READING");
  } else {
    Serial.print(distance, 2);
    Serial.print(" cm | Baseline: ");
    Serial.print(baselineDistance, 2);
    Serial.print(" cm | Delta: ");
    Serial.print(distance - baselineDistance, 2);
    Serial.println(" cm");
  }

  if (smsStep == SMS_IDLE && !smsSent && distance < (baselineDistance - DETECTION_OFFSET_CM)) {
    String smsMsg = "CRAYFISH DETECTED INSIDE HIDE.\nBehavior: Occupying Shelter.\nDistance: " + String(distance, 2) + " cm";
    Serial.println("[ALERT] Crayfish inside hide -- starting SMS (non-blocking)");
    mqttPublish(TOPIC_ALERT, "CRAYFISH_INSIDE_HIDE");
    smsBegin(smsMsg);
  }

  if (smsSent && smsStep == SMS_IDLE && distance > (baselineDistance - 1.0)) {
    smsSent = false;
    Serial.println("[ALERT] Crayfish exited hide -- SMS reset.");
  }

  // --- 3. pH (150ms) ---
  float raw     = getAvgPHRaw();
  float voltage = (raw / 4095.0) * 3.3;
  float pH      = 7.0 + SLOPE * (voltage - VOLTAGE_AT_7);
  mqttPublish(TOPIC_PH, pH);

  Serial.print("[SENSOR] pH        : ");
  Serial.print(pH, 2);
  Serial.print(" (raw=");
  Serial.print(raw, 0);
  Serial.print(", voltage=");
  Serial.print(voltage, 3);
  Serial.println(" V)");

  if (pH < PH_SAFE_LOW) {
    Serial.println("[pH] WARNING: Below safe range!");
  } else if (pH > PH_SAFE_HIGH) {
    Serial.println("[pH] WARNING: Above safe range!");
  } else {
    Serial.println("[pH] Within safe range.");
  }

  if (ctrlAirPump.mode == AUTO) {
    bool outOfRange = pH < (PH_SAFE_LOW - 0.1) || pH > (PH_SAFE_HIGH + 0.1);
    bool inRange    = pH >= PH_SAFE_LOW && pH <= PH_SAFE_HIGH;
    if (!airPumpOn && outOfRange) setAirPump(true);
    if ( airPumpOn && inRange)    setAirPump(false);
  }

  // --- 4. Turbidity (instant) ---
  int turbidity = analogRead(TURBIDITY_PIN);
  mqttPublish(TOPIC_TURBIDITY, (float)turbidity);

  Serial.print("[SENSOR] Turbidity : ");
  Serial.print(turbidity);
  if (turbidity > TURBIDITY_THRESHOLD) {
    Serial.println(" -- DIRTY (pump ON)");
  } else {
    Serial.println(" -- CLEAR (pump OFF)");
  }

  if (ctrlPump.mode == AUTO) {
    bool shouldPump = turbidity > TURBIDITY_THRESHOLD;
    if (shouldPump != lastPumpState) {
      setPump(shouldPump);
      lastPumpState = shouldPump;
    }
  }

  // --- 5. Light (~120ms) ---
  float lux = lightMeter.readLightLevel();
  mqttPublish(TOPIC_LUX, lux);

  Serial.print("[SENSOR] Lux       : ");
  Serial.print(lux, 2);
  Serial.print(" lx  (ON < ");
  Serial.print(LUX_LED_ON, 0);
  Serial.print(" lx / OFF > ");
  Serial.print(LUX_LED_OFF, 0);
  Serial.print(" lx)  -- ");

  if (ctrlLED.mode == AUTO) {
    bool shouldBeOn;

    if      (!ledCurrentlyOn && lux < LUX_LED_ON)   shouldBeOn = true;
    else if ( ledCurrentlyOn && lux > LUX_LED_OFF)  shouldBeOn = false;
    else                                              shouldBeOn = ledCurrentlyOn;

    Serial.println(shouldBeOn ? "DARK  -> LED ON" : "BRIGHT -> LED OFF");
    applyLED(shouldBeOn);
  } else {
    Serial.println(ctrlLED.manualState ? "MANUAL ON" : "MANUAL OFF");
    applyLED(ctrlLED.manualState);
  }

  // --- 6. Temperature (blocking read, reliable) ---
  tempSensor.requestTemperatures();
  float temp = tempSensor.getTempCByIndex(0);
  mqttPublish(TOPIC_TEMP, temp);

  Serial.print("[SENSOR] Temp      : ");
  Serial.print(temp, 2);
  Serial.println(" C");

  if (temp >= COOL_ON_TEMP) {
    Serial.println("[TEMP] WARNING: High temp -- cooling ON.");
  } else if (temp <= COOL_OFF_TEMP) {
    Serial.println("[TEMP] Temp normal -- cooling OFF.");
  }

  if (ctrlCooling.mode == AUTO) {
    if (temp >= COOL_ON_TEMP)  setCooling(true);
    if (temp <= COOL_OFF_TEMP) setCooling(false);
  }

  // --- 7. Feeder (Auto via Serial command) ---
  if (ctrlFeeder.mode == AUTO && Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "STEPPER_ROTATE") {
      runFeeder("AUTO");
    }
  }

  // --- 8. Batch publish all sensor readings in ONE MQTT message ---
  String batch = "{\"ph\":" + String(pH, 2)
    + ",\"temp\":" + String(temp, 2)
    + ",\"turbidity\":" + String(turbidity)
    + ",\"lux\":" + String(lux, 2)
    + ",\"distance\":" + String(distance, 2)
    + "}";
  mqttPublish(TOPIC_SENSORS_BATCH, batch.c_str());

  Serial.println("=====================================");
}
