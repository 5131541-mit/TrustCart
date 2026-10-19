// TrustCart smart shelf node: ESP32 + HX711 load cell. Sends weight changes to the server; queues them if Wi-Fi drops.
// Libraries: "HX711" by bogde (Arduino Library Manager). Board: ESP32 Dev Module.
// Calibrate: set CAL to 1, put a known weight on, read scale.get_units(10), then CAL = reading / known_grams.
#include <WiFi.h>
#include <HTTPClient.h>
#include "HX711.h"

const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* SERVER    = "http://192.168.1.50:8000/api/shelf";   // your computer running server.py
const char* DEVICE_KEY = "YOUR_DEVICE_API_KEY";                   // same as DEVICE_API_KEY on the server
const char* SHELF_ID  = "shelf-A1";                              // must match SHELVES in server.py
const int   DT_PIN = 16, SCK_PIN = 4;
const float CAL = 420.0f;            // counts per gram (calibrate your own load cell)
const float MIN_DELTA_G = 15.0f;     // ignore changes smaller than this
const float SETTLE_G = 3.0f;         // reading must stay within this band...
const unsigned long SETTLE_MS = 700; // ...for this long before we trust it

HX711 scale;
float baseline = 0, last = 0;
unsigned long settledSince = 0, lastWifiTry = 0;
float queueBuf[32]; int queued = 0;  // offline-first: keep events until Wi-Fi returns

bool sendDelta(float d) {
  if (WiFi.status() != WL_CONNECTED) return false;
  HTTPClient http; http.begin(SERVER); http.addHeader("Content-Type", "application/json"); http.addHeader("X-API-Key", DEVICE_KEY);
  String body = String("{\"shelf_id\":\"") + SHELF_ID + "\",\"delta_g\":" + String(d, 1) + "}";
  int code = http.POST(body); http.end();
  return code >= 200 && code < 300;
}

void setup() {
  Serial.begin(115200);
  scale.begin(DT_PIN, SCK_PIN); scale.set_scale(CAL); scale.tare();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  baseline = last = scale.get_units(10);
}

void loop() {
  float w = scale.get_units(3); unsigned long now = millis();
  if (fabs(w - last) > SETTLE_G) { settledSince = now; last = w; }          // still moving
  if (now - settledSince > SETTLE_MS && fabs(last - baseline) >= MIN_DELTA_G) {
    float d = last - baseline; baseline = last;                              // negative = item removed
    Serial.printf("delta %.1f g\n", d);
    if (!sendDelta(d) && queued < 32) queueBuf[queued++] = d;
  }
  if (queued > 0 && sendDelta(queueBuf[0])) { memmove(queueBuf, queueBuf + 1, (--queued) * sizeof(float)); }
  if (WiFi.status() != WL_CONNECTED && now - lastWifiTry > 10000) { lastWifiTry = now; WiFi.reconnect(); }
  delay(100);
}
