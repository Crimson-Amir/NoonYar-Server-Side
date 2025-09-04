#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <LittleFS.h>
#include <vector>
#include <PubSubClient.h>
#include <algorithm>
#include <numeric>

#define MAX_KEYS 10
#define MAX_WIFI_RETRIES 5
#define MAX_HTTP_RETRIES 5

Preferences prefs;

const char* ssid = "Netenza_FDC1D0";
const char* password = "aA12345!";

const char* bakery_id = "1";
const char* token = "UQ0IYlmGWJbn-myt2sZtMKqgKSVBjGx18tGLxaB4aNs";

int breads_id[MAX_KEYS];
int bread_cook_time[MAX_KEYS];
int data_count = 0;

bool init_success = false;
std::vector<int> reservation_keys;

WiFiClient espClient;
PubSubClient client(espClient);

const char* mqtt_server = "broker.emqx.io";
const int mqtt_port = 1883;
String mqtt_client_id = "bakery-" + String(ESP.getChipModel()) + "-" + String(ESP.getChipId());
String topic_errors = "bakery/" + bakery_id + "/errors";
String bread_time_update = "bakery/" + String(bakery_id) + "/bread_time_update";

// ========== UTILITIES ==========

unsigned long lastWifiAttempt = 0;
unsigned long lastMqttAttempt = 0;

void ensureConnectivity() {
  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - lastWifiAttempt > 5000) {
      Serial.println("Trying WiFi...");
      WiFi.disconnect();
      WiFi.begin(ssid, password);
      lastWifiAttempt = millis();
    }
    digitalWrite(LED_PIN, (millis() / 300) % 2);
    return;
  }

  // MQTT connect
  if (!client.connected()) {
    if (millis() - lastMqttAttempt > 3000) {
      Serial.println("Trying MQTT...");
      if (client.connect(mqtt_client_id.c_str(), mqtt_user, mqtt_password)) {
        Serial.println("MQTT connected.");
        digitalWrite(LED_PIN, HIGH);
        client.subscribe(bread_time_update.c_str());
      } else {
        Serial.printf("MQTT failed rc=%d\n", client.state());
      }
      lastMqttAttempt = millis();
    }
    digitalWrite(LED_PIN, (millis() / 800) % 2);
    return;
  }

  client.loop();
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String message;
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }

  String expected = "bakery/" + String(bakery_id) + "/bread_time_update";
  if (String(topic) == expected) {
    Serial.println("Bread time update received → refreshing init data");

    xTaskCreatePinnedToCore(fetchInitTask, "fetchInitTask", 4096, NULL, 1, NULL, 1);
  }
}


int vectorSum(const std::vector<int>& vec) {
  return std::accumulate(vec.begin(), vec.end(), 0);
}

std::vector<int>* findReservationByKey(int key) {
  for (auto& pair : reservation_dict) {
    if (pair.first == key) return &pair.second;
  }
  return nullptr;
}

std::vector<int>* getReservationByKey(int key) {
  for (auto& [k, v] : reservation_dict) {
    if (k == key) return &v;
  }
  return nullptr;
}

// ========== RESERVATION LOGIC ==========

String sendHttpRequest(const String& url, const String& method, const String& body = "") {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi not connected.");
    return "";
  }

  HTTPClient http;
  http.begin(url);
  http.addHeader("Authorization", "Bearer " + String(token));
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Connection", "keep-alive");
  http.setTimeout(15000); // 15s

  int httpCode = -1;
  if (method == "GET") {
    httpCode = http.GET();
  } else if (method == "POST") {
    httpCode = http.POST(body);
  } else if (method == "PUT") {
    httpCode = http.PUT(body);
  }

  String payload;
  if (httpCode > 0) {
    payload = http.getString();
    Serial.printf("[%s] code=%d\n", url.c_str(), httpCode);
  } else {
    Serial.printf("[%s] request failed: %s\n", url.c_str(), http.errorToString(httpCode).c_str());
  }

  http.end();
  return payload;
}

int NewCustomerServer(const std::vector<int>& breads) {
  StaticJsonDocument<512> doc;
  doc["bakery_id"] = atoi(bakery_id);

  JsonObject breadMap = doc.createNestedObject("bread_requirements");
  for (int i = 0; i < data_count; ++i) {
    breadMap[String(breads_id[i])] = breads[i];
  }

  String body;
  serializeJson(doc, body);

  String resp = sendHttpRequest("http://united-kingdom.oopsididitagain.site:8000/hc/new_customer", "POST", body);
  if (resp == "") return -1;

  StaticJsonDocument<256> respDoc;
  if (deserializeJson(respDoc, resp)) return -1;

  return respDoc["customer_id"] | -1;
}

struct NextTicketResponse {
  int current_ticket_id;
  bool skipped_customer;
  std::map<int,int> breads;
  String error;
};
NextTicketResponse requestNextTicket(int customer_ticket_id) {
  NextTicketResponse resp = {-1, false, {}, ""};

  StaticJsonDocument<256> bodyDoc;
  bodyDoc["bakery_id"] = atoi(bakery_id);
  bodyDoc["customer_ticket_id"] = customer_ticket_id;

  String body;
  serializeJson(bodyDoc, body);

  String response = sendHttpRequest("http://noonyar.ir/hc/nt", "PUT", body);
  if (response == "") return resp;

  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, response)) {
    resp.error = "Invalid JSON";
    return resp;
  }

  resp.current_ticket_id = doc["current_ticket_id"] | -1;
  resp.skipped_customer = doc["skipped_customer"] | false;
  for (JsonPair kv : doc["current_user_detail"].as<JsonObject>()) {
    resp.breads[String(kv.key().c_str()).toInt()] = kv.value().as<int>();
  }

  return resp;
}


struct CurrentTicketResponse {
  int current_ticket_id;
  std::map<int,int> breads;
  String error;
};
CurrentTicketResponse getCurrentTicket() {
  CurrentTicketResponse resp = {-1, {}, ""};

  String response = sendHttpRequest("http://noonyar.ir/hc/ct/" + String(bakery_id), "GET");
  if (response == "") return resp;

  StaticJsonDocument<512> doc;
  if (deserializeJson(doc, response)) {
    resp.error = "Invalid JSON";
    return resp;
  }

  resp.current_ticket_id = doc["current_ticket_id"] | -1;
  for (JsonPair kv : doc["current_user_detail"].as<JsonObject>()) {
    resp.breads[String(kv.key().c_str()).toInt()] = kv.value().as<int>();
  }

  return resp;
}

bool skipTicket(int customer_ticket_id) {
  StaticJsonDocument<256> bodyDoc;
  bodyDoc["bakery_id"] = atoi(bakery_id);
  bodyDoc["customer_ticket_id"] = customer_ticket_id;

  String body;
  serializeJson(bodyDoc, body);

  String response = sendHttpRequest("http://noonyar.ir/hc/ct/st", "PUT", body);
  return response != ""; // success if server replied anything
}

void newCustomerTask(void *param) {
  std::vector<int>* breads = (std::vector<int>*)param;
  int customerId = NewCustomerServer(*breads);

  if (customerId != -1) {
    Serial.printf("✅ New customer created, ID=%d\n", customerId);
  } else {
    client.publish(topic_errors.c_str(), "❌ NewCustomer failed");
  }

  delete breads; // free heap (since we passed a new'd vector)
  vTaskDelete(NULL);
}


void requestNextTicketTask(void *param) {
  int ticketId = *(int*)param;
  NextTicketResponse r = requestNextTicket(ticketId);

  if (r.current_ticket_id != -1) {
    Serial.printf("✅ Next ticket: %d\n", r.current_ticket_id);
  } else {
    client.publish(topic_errors.c_str(), ("❌ NextTicket failed: " + r.error).c_str());
  }

  delete (int*)param; // free heap
  vTaskDelete(NULL);
}


void currentTicketTask(void *param) {
  CurrentTicketResponse r = getCurrentTicket();

  if (r.current_ticket_id != -1) {
    Serial.printf("✅ Current ticket: %d\n", r.current_ticket_id);
  } else {
    client.publish(topic_errors.c_str(), ("❌ CurrentTicket failed: " + r.error).c_str());
  }

  vTaskDelete(NULL);
}


void skipTicketTask(void *param) {
  int ticketId = *(int*)param;
  bool ok = skipTicket(ticketId);

  if (ok) {
    Serial.printf("✅ Ticket %d skipped\n", ticketId);
  } else {
    client.publish(topic_errors.c_str(), "❌ SkipTicket failed");
  }

  delete (int*)param; // free heap
  vTaskDelete(NULL);
}


// ========== FLASH PREFS ==========


void saveInitDataToFlash() {
  if (!prefs.begin("bakery_data", false)) {
    Serial.println("Prefs init failed!");
    return;
  }

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


// ========== NETWORK FETCH ==========

void fetchInitData() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi not connected. Reconnecting...");
    connectToWiFi();
  }
  HTTPClient http;
  String url = "http://united-kingdom.oopsididitagain.site:8000/hc/hardware_init?bakery_id=" + String(bakery_id);
  http.begin(url);
  http.addHeader("Connection", "keep-alive");
  http.setTimeout(7000);

  int tries = 0, httpCode = -1;
  while (tries < MAX_HTTP_RETRIES && httpCode <= 0 && WiFi.status() == WL_CONNECTED) {
    httpCode = http.GET();
    if (httpCode <= 0) {
      Serial.println("Retrying HTTP...");
      delay(1000);
      tries++;
    }
  }

  if (httpCode == 200) {
    String payload = http.getString();

    StaticJsonDocument<512> doc;

    DeserializationError error = deserializeJson(doc, payload);
    if (error) {
      return false;
    }

    data_count = 0;
    for (JsonPair kv : doc.as<JsonObject>()) {
      if (data_count >= MAX_KEYS) break;
      breads_id[data_count] = String(kv.key().c_str()).toInt();
      bread_cook_time[data_count] = kv.value().as<int>();
      data_count++;
    }

    saveInitDataToFlash();
  }
  http.end();
  return true
}

void fetchInitTask(void *param) {
  while (!fetchInitData()) {
    client.publish(topic_errors.c_str(), "fetchInitData:failed");
    init_success = false;
    vTaskDelay(5000 / portTICK_PERIOD_MS);
  }
  init_success = true;
  vTaskDelete(NULL);
}


// ========== SETUP / LOOP ==========

void setup() {
  Serial.begin(115200);
  LittleFS.begin();

  xTaskCreatePinnedToCore(
    fetchInitTask, "FetchInitTask", 4096, NULL, 1, NULL, 1
  );

  client.setCallback(mqttCallback);

}

void loop() {
  ensureConnectivity();

}
