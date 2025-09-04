#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <LittleFS.h>
#include <vector>
#include <PubSubClient.h>
#include <LedControl.h>   // MAX7219

// ---------- CONFIG ----------
#define MAX_KEYS          10
#define MAX_HTTP_RETRIES  5

// MAX7219 pins (edit if needed)
#define DIN_PIN  23
#define CLK_PIN  18
#define CS_PIN   5

// WiFi / API / MQTT
const char* ssid       = "Netenza_FDC1D0";
const char* password   = "aA12345!";

const char* bakery_id  = "1";
const char* token      = "UQ0IYlmGWJbn-myt2sZtMKqgKSVBjGx18tGLxaB4aNs";

const char* mqtt_server = "broker.emqx.io";
const int   mqtt_port   = 1883;

String topic_errors      = String("bakery/") + bakery_id + "/errors";
String topic_bread_time  = String("bakery/") + bakery_id + "/bread_time_update";

// ---------- GLOBAL STATE ----------
Preferences prefs;
WiFiClient   net;
PubSubClient mqtt(net);
LedControl   lc(DIN_PIN, CLK_PIN, CS_PIN, 1);

int breads_id[MAX_KEYS];
int bread_cook_time[MAX_KEYS];
int data_count = 0;

volatile bool init_success = false;

// display state
enum DeviceStatus : uint8_t {
  STATUS_NORMAL,
  STATUS_WIFI_ERROR,
  STATUS_MQTT_ERROR,
  STATUS_API_WAITING,
  STATUS_API_ERROR
};

volatile DeviceStatus currentStatus = STATUS_NORMAL;
int num1 = 0;
int num2 = 0;
int num3 = 0;
unsigned long lastBlinkMs = 0;
bool blinkOn = true;

// busy mutex to prevent overlapping API jobs
SemaphoreHandle_t busyMutex;

// reconnect pacing
unsigned long lastWifiAttempt = 0;
unsigned long lastMqttAttempt = 0;


// ====== BUSY HELPERS ======
SemaphoreHandle_t busyMutex;

bool networkBlock = false;

void setNetworkBlock(bool enable) {
  if (enable && !networkBlock) {
    tryLockBusy();   // lock once
    networkBlock = true;
  } else if (!enable && networkBlock) {
    unlockBusy();    // unlock once
    networkBlock = false;
  }
}

// Try to lock, return true if locked successfully
bool tryLockBusy() {
    return xSemaphoreTake(busyMutex, (TickType_t)0) == pdTRUE;
}

// Unlock
void unlockBusy() {
    xSemaphoreGive(busyMutex);
}

// Just check if busy (locked) without taking or modifying the semaphore
bool isBusyNow() {
    // Try take with zero timeout; if success, immediately give it back
    if (xSemaphoreTake(busyMutex, (TickType_t)0) == pdTRUE) {
        xSemaphoreGive(busyMutex);
        return false; // was not busy
    }
    return true; // currently locked
}
// <----- HERE

// ---------- UTILS: DISPLAY OVERLAY ----------

void displayChar(char c) {
  lc.clearDisplay(0);
  lc.setChar(0, 0, c, false);
}

void displayDash() {
  lc.clearDisplay(0);
  lc.setChar(0, 0, '-', false);
}

void showNumbers(int a, int b, int c) {
  if (currentStatus == STATUS_NORMAL) {
    lc.clearDisplay(0);
    lc.setDigit(0, 0, a % 10, false);  // rightmost
    lc.setDigit(0, 1, b % 10, false);  // middle
    lc.setDigit(0, 2, c % 10, false);  // leftmost
  }
}

void setStatus(DeviceStatus st) {
  currentStatus = st;
  lc.clearDisplay(0);

  if (st == STATUS_NORMAL) {
    showNumbers(num1, num2, num3);
  }
  else if (st == STATUS_WIFI_ERROR) {
    displayChar('W');
  }
  else if (st == STATUS_MQTT_ERROR) {
    displayChar('M');
  }
  else if (st == STATUS_API_ERROR) {
    displayChar('E');
  }
  else if (st == STATUS_API_WAITING) {
    displayDash();
  }
}

// ---------- FLASH (Preferences) ----------
void saveInitDataToFlash() {
  if (!prefs.begin("bakery_data", false)) return;
  prefs.putInt("cnt", data_count);
  char key[8];
  for (int i = 0; i < data_count; i++) {
    snprintf(key, sizeof(key), "k%d", i);
    prefs.putInt(key, breads_id[i]);
    snprintf(key, sizeof(key), "v%d", i);
    prefs.putInt(key, bread_cook_time[i]);
  }
  prefs.end();
}

// ---------- CONNECTIVITY ----------
bool isNetworkReady() {
  return WiFi.status() == WL_CONNECTED && mqtt.connected();
}

void ensureConnectivity() {
  // WiFi
  if (WiFi.status() != WL_CONNECTED) {
    setStatus(STATUS_WIFI_ERROR);
    setNetworkBlock(true);
    if (millis() - lastWifiAttempt > 3500) {
      lastWifiAttempt = millis();
      WiFi.disconnect();
      WiFi.begin(ssid, password);
    }
  } else {
    if (!mqtt.connected()) {
      if (millis() - lastMqttAttempt > 2500) {
        lastMqttAttempt = millis();
        if (mqtt.connect(bakery_id)) {
          mqtt.subscribe(topic_bread_time.c_str());
          setStatus(STATUS_NORMAL);
        } else {
          setNetworkBlock(true);
          etStatus(STATUS_MQTT_ERROR);
        }
      }
    } else {
      setNetworkBlock(false)
      setStatus(STATUS_NORMAL);
      mqtt.loop();
    }
  }
}

// ---------- MQTT ----------
void mqttPublishError(const String& msg) {
  if (mqtt.connected()) {
    mqtt.publish(topic_errors.c_str(), msg.c_str(), true);
  }
}

void fetchInitFromMqttTask(void* param) {
    if (!tryLockBusy()) {
        client.publish(topic_errors.c_str(), "❌ Device busy");
        vTaskDelete(NULL);
    }

    bool ok = fetchInitData();
    if (!ok){client.publish(topic_errors.c_str(), "failed");}
    unlockBusy();
    vTaskDelete(NULL);
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
    if (String(topic) == bread_time_update) {
        xTaskCreatePinnedToCore(
            fetchInitFromMqttTask, // simple function
            "FetchInitOnMqtt", 4096, NULL, 1, NULL, 1
        );
    }
}

// ---------- HTTP CORE ----------
String sendHttpRequest(const String& url, const char* method, const String& body = "", uint16_t timeoutMs = 15000) {
  if (WiFi.status() != WL_CONNECTED) {
    return -1;
  }

  HTTPClient http;
  http.begin(url);
  http.addHeader("Authorization", String("Bearer ") + token);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Connection", "keep-alive");
  http.setTimeout(timeoutMs);

  int code = -1;
  if      (!strcmp(method, "GET"))  code = http.GET();
  else if (!strcmp(method, "POST")) code = http.POST(body);
  else if (!strcmp(method, "PUT"))  code = http.PUT(body);

  String payload;
  if (code > 0) {
    payload = http.getString();
    if (code != 200) {
      mqttPublishError(String("http_request:") + url);
      payload = -1;
    }
  } else {
    mqttPublishError(String("http_fail:") + http.errorToString(code));
  }

  http.end();
  return payload;
}

// ---------- API CALLS ----------
bool fetchInitData() {
  if (WiFi.status() != WL_CONNECTED) return false;

  String url = String("http://united-kingdom.oopsididitagain.site:8000/hc/hardware_init?bakery_id=") + bakery_id;

  int tries = 0;
  while (tries < MAX_HTTP_RETRIES) {
    String resp = sendHttpRequest(url, "GET", "", 7000);
    if (resp.length() == 0) {
      tries++;
      vTaskDelay(800 / portTICK_PERIOD_MS);
      continue;
    }

    StaticJsonDocument<768> doc;
    DeserializationError err = deserializeJson(doc, resp);
    if (err) {
      mqttPublishError(String("json_err:init:") + err.c_str());
      return false;
    }

    data_count = 0;
    for (JsonPair kv : doc.as<JsonObject>()) {
      if (data_count >= MAX_KEYS) break;
      breads_id[data_count]       = String(kv.key().c_str()).toInt();
      bread_cook_time[data_count] = kv.value().as<int>();
      data_count++;
    }

    saveInitDataToFlash();
    return true;
  }

  mqttPublishError("init_fetch_failed");
  return false;
}

int apiNewCustomer(const std::vector<int>& breads) {
  // Build body
  StaticJsonDocument<512> bodyDoc;
  bodyDoc["bakery_id"] = atoi(bakery_id);
  JsonObject req = bodyDoc.createNestedObject("bread_requirements");
  for (int i = 0; i < data_count; ++i) {
    req[String(breads_id[i])] = (i < (int)breads.size() ? breads[i] : 0);
  }
  String body; serializeJson(bodyDoc, body);

  String resp = sendHttpRequest("http://united-kingdom.oopsididitagain.site:8000/hc/new_customer", "POST", body);
  if (resp.length() == 0) return -1;

  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, resp);
  if (err) { mqttPublishError(String("json_err:nc:") + err.c_str()); return -1; }

  return doc["customer_id"] | -1;
}

struct NextTicketResponse {
  int current_ticket_id = -1;
  bool skipped_customer = false;
  // map bread_id -> qty
  std::map<int,int> breads;
  String error;
};

NextTicketResponse apiNextTicket(int customer_ticket_id) {
  NextTicketResponse r;

  StaticJsonDocument<256> bodyDoc;
  bodyDoc["bakery_id"] = atoi(bakery_id);
  bodyDoc["customer_ticket_id"] = customer_ticket_id;
  String body; serializeJson(bodyDoc, body);

  String resp = sendHttpRequest("http://noonyar.ir/hc/nt", "PUT", body);
  if (resp.length() == 0) { r.error = "http_fail"; return r; }

  StaticJsonDocument<768> doc;
  DeserializationError err = deserializeJson(doc, resp);
  if (err) { r.error = String("json_err:") + err.c_str(); return r; }

  r.current_ticket_id = doc["current_ticket_id"] | -1;
  r.skipped_customer  = doc["skipped_customer"]  | false;

  JsonObject detail = doc["current_user_detail"].as<JsonObject>();
  for (JsonPair kv : detail) {
    r.breads[String(kv.key().c_str()).toInt()] = kv.value().as<int>();
  }

  return r;
}

struct CurrentTicketResponse {
  int current_ticket_id = -1;
  std::map<int,int> breads;
  String error;
};

CurrentTicketResponse apiCurrentTicket() {
  CurrentTicketResponse r;

  String resp = sendHttpRequest(String("http://noonyar.ir/hc/ct/") + bakery_id, "GET");
  if (resp.length() == 0) { r.error = "http_fail"; return r; }

  StaticJsonDocument<768> doc;
  DeserializationError err = deserializeJson(doc, resp);
  if (err) { r.error = String("json_err:") + err.c_str(); return r; }

  r.current_ticket_id = doc["current_ticket_id"] | -1;
  JsonObject detail = doc["current_user_detail"].as<JsonObject>();
  for (JsonPair kv : detail) {
    r.breads[String(kv.key().c_str()).toInt()] = kv.value().as<int>();
  }
  return r;
}

bool apiSkipTicket(int customer_ticket_id) {
  StaticJsonDocument<256> bodyDoc;
  bodyDoc["bakery_id"] = atoi(bakery_id);
  bodyDoc["customer_ticket_id"] = customer_ticket_id;
  String body; serializeJson(bodyDoc, body);

  String resp = sendHttpRequest("http://noonyar.ir/hc/ct/st", "PUT", body);
  return resp.length() != 0; // any 200 response == success
}

// ---------- TASKS (non-blocking jobs) ----------
void newCustomerTask(void *param) {
  std::vector<int>* breads = (std::vector<int>*)param;

  if (xSemaphoreTake(busyMutex, (TickType_t)0) != pdTRUE) {
    mqttPublishError("busy:new_customer");
    delete breads;
    vTaskDelete(NULL);
  }

  setStatus(STATUS_API_WAITING);
  int cid = apiNewCustomer(*breads);
  setStatus(cid != -1 ? STATUS_NORMAL : STATUS_API_ERROR);
  if (cid == -1) mqttPublishError("nc:failed");

  delete breads;
  xSemaphoreGive(busyMutex);
  vTaskDelete(NULL);
}

void nextTicketTask(void *param) {
  int ticketId = *(int*)param;
  delete (int*)param;

  if (xSemaphoreTake(busyMutex, (TickType_t)0) != pdTRUE) {
    mqttPublishError("busy:next_ticket");
    vTaskDelete(NULL);
  }

  setStatus(STATUS_API_WAITING);
  NextTicketResponse r = apiNextTicket(ticketId);
  bool ok = (r.current_ticket_id != -1);
  setStatus(ok ? STATUS_NORMAL : STATUS_API_ERROR);
  if (!ok) mqttPublishError(String("nt:failed:") + r.error);

  xSemaphoreGive(busyMutex);
  vTaskDelete(NULL);
}

void currentTicketTask(void *param) {
  if (xSemaphoreTake(busyMutex, (TickType_t)0) != pdTRUE) {
    mqttPublishError("busy:current_ticket");
    vTaskDelete(NULL);
  }

  setStatus(STATUS_API_WAITING);
  CurrentTicketResponse r = apiCurrentTicket();
  bool ok = (r.current_ticket_id != -1);
  setStatus(ok ? STATUS_NORMAL : STATUS_API_ERROR);
  if (!ok) mqttPublishError(String("ct:failed:") + r.error);

  xSemaphoreGive(busyMutex);
  vTaskDelete(NULL);
}

void skipTicketTask(void *param) {
  int ticketId = *(int*)param;
  delete (int*)param;

  if (xSemaphoreTake(busyMutex, (TickType_t)0) != pdTRUE) {
    mqttPublishError("busy:skip_ticket");
    vTaskDelete(NULL);
  }

  setStatus(STATUS_API_WAITING);
  bool ok = apiSkipTicket(ticketId);
  setStatus(ok ? STATUS_NORMAL : STATUS_API_ERROR);
  if (!ok) mqttPublishError("st:failed");

  xSemaphoreGive(busyMutex);
  vTaskDelete(NULL);
}

// ---------- SETUP / LOOP ----------
void setup() {
  // Filesystem (optional)
  LittleFS.begin();

  // MAX7219 init
  lc.shutdown(0, false);
  lc.setIntensity(0, 8); // 0..15
  lc.clearDisplay(0);

  // Busy mutex
  busyMutex = xSemaphoreCreateMutex();

  // WiFi (non-blocking reconnection in loop)
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  // MQTT
  mqtt.setServer(mqtt_server, mqtt_port);
  mqtt.setCallback(mqttCallback);

  // Load cached init data first (so device can work offline)
  if (!loadInitDataFromFlash()) {
    // if nothing cached yet, try to fetch async
    xTaskCreatePinnedToCore(
      [](void*){
        if (xSemaphoreTake(busyMutex, (TickType_t)0) == pdTRUE) {
          setStatus(STATUS_API_WAITING);
          bool ok = fetchInitData();
          setStatus(ok ? STATUS_NORMAL : STATUS_API_ERROR);
          xSemaphoreGive(busyMutex);
        } else {
          mqttPublishError("busy:init_on_boot");
        }
        vTaskDelete(NULL);
      }, "InitFetchBoot", 4096, NULL, 1, NULL, 1
    );
  } else {
    setStatus(STATUS_NORMAL);
  }

  // show some default number until your app sets it
  showNumber(0);
}

void loop() {
  ensureConnectivity();
  updateDisplay();

  // ... your normal app updates here ...
  // Example: showNumber(currentTicket); when your app changes it.
}

// ---------- PUBLIC HELPERS YOU CAN CALL ----------
void startNewCustomer(const std::vector<int>& breads) {
  auto *heapVec = new std::vector<int>(breads);
  xTaskCreatePinnedToCore(newCustomerTask, "NewCustomer", 4096, heapVec, 1, NULL, 1);
}
void startNextTicket(int ticketId) {
  int* heapId = new int(ticketId);
  xTaskCreatePinnedToCore(nextTicketTask, "NextTicket", 4096, heapId, 1, NULL, 1);
}
void startCurrentTicket() {
  xTaskCreatePinnedToCore(currentTicketTask, "CurrentTicket", 4096, NULL, 1, NULL, 1);
}
void startSkipTicket(int ticketId) {
  int* heapId = new int(ticketId);
  xTaskCreatePinnedToCore(skipTicketTask, "SkipTicket", 4096, heapId, 1, NULL, 1);
}
