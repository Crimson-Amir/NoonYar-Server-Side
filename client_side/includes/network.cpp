#include "network.h"
#include "mqtt.h"
#include "display.h"

// ---------- GLOBAL NETWORK OBJECTS ----------
WiFiClient net;
PubSubClient mqtt(net);
String topic_errors = String("bakery/") + bakery_id + "/errors";
String topic_bread_time = String("bakery/") + bakery_id + "/bread_time_update";

// ---------- NETWORK STATE MANAGEMENT ----------
SemaphoreHandle_t networkBlockMutex;
bool networkBlock = false;
unsigned long lastWifiAttempt = 0;
unsigned long lastMqttAttempt = 0;

void setNetworkBlock(bool enable) {
  if (enable && !networkBlock) {
    xSemaphoreTake(networkBlockMutex, portMAX_DELAY);
    networkBlock = true;
  } else if (!enable && networkBlock) {
    xSemaphoreGive(networkBlockMutex);
    networkBlock = false;
  }
}

bool isNetworkBlocked() {
  if (xSemaphoreTake(networkBlockMutex, (TickType_t)0) == pdTRUE) {
    xSemaphoreGive(networkBlockMutex);
    return false;
  }
  return true;
}

bool isNetworkReadyForApi() {
  return WiFi.status() == WL_CONNECTED && mqtt.connected() && !isNetworkBlocked();
}

bool isNetworkReady() {
  return WiFi.status() == WL_CONNECTED && mqtt.connected();
}

void ensureConnectivity() {
  if (WiFi.status() != WL_CONNECTED) {
    setStatus(STATUS_WIFI_ERROR);
    setNetworkBlock(true);
    if (millis() - lastWifiAttempt > WIFI_RECONNECT_INTERVAL) {
      lastWifiAttempt = millis();
      WiFi.disconnect();
      WiFi.begin(ssid, password);
    }
  } else {
    if (!mqtt.connected()) {
      if (millis() - lastMqttAttempt > MQTT_RECONNECT_INTERVAL) {
        lastMqttAttempt = millis();
        if (mqtt.connect(bakery_id)) {
          mqtt.subscribe(topic_bread_time.c_str());
          setStatus(STATUS_NORMAL);
        } else {
          setNetworkBlock(true);
          setStatus(STATUS_MQTT_ERROR);
        }
      }
    } else {
      setNetworkBlock(false);
      setStatus(STATUS_NORMAL);
      mqtt.loop();
    }
  }
}

String sendHttpRequest(const String& url, const char* method, const String& body, uint16_t timeoutMs) {
  if (WiFi.status() != WL_CONNECTED) return String();

  HTTPClient http;
  http.begin(url);
  http.addHeader("Authorization", "Bearer " + String(token));
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Connection", "keep-alive");
  http.setTimeout(timeoutMs);

  int code = -1;
  if      (!strcmp(method, "GET"))  code = http.GET();
  else if (!strcmp(method, "POST")) code = http.POST(body);
  else if (!strcmp(method, "PUT"))  code = http.PUT(body);

  String payload;
  if (code > 0 && (code >= 200 && code < 300)) {
    payload = http.getString();
  } else {
    payload = String();
    Serial.println("HTTP request failed with code: " + String(code));
  }

  http.end();
  return payload;
}
