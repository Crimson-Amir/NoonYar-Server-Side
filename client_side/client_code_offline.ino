#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <LittleFS.h>
#include <vector>
#include <algorithm>
#include <numeric>

#define MAX_KEYS 10
#define MAX_WIFI_RETRIES 5
#define MAX_HTTP_RETRIES 3

Preferences prefs;

// Changed from std::map to vector of pairs for memory efficiency and deterministic order
std::vector<std::pair<int, std::vector<int>>> reservation_dict;

const char* ssid = "Netenza_FDC1D0";
const char* password = "aA12345!";

const char* bakery_id = "1";
const char* token = "UQ0IYlmGWJbn-myt2sZtMKqgKSVBjGx18tGLxaB4aNs";

int breads_id[MAX_KEYS];
int bread_cook_time[MAX_KEYS];
int data_count = 0;

bool dataChanged = false;
unsigned long lastSaveTime = 0;
std::vector<int> reservation_keys;

// ========== UTILITIES ==========

void rebuildReservationKeys() {
  reservation_keys.clear();
  for (const auto& pair : reservation_dict) {
    reservation_keys.push_back(pair.first);
  }
  std::sort(reservation_keys.begin(), reservation_keys.end());
}


void connectToWiFi() {
  Serial.print("Connecting to WiFi");
  WiFi.begin(ssid, password);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < MAX_WIFI_RETRIES) {
    delay(1000);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nConnected to WiFi.");
  } else {
    Serial.println("\nWiFi connection failed.");
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


// ========== LITTLEFS ==========

void saveReservationsToLittleFS() {
  StaticJsonDocument<4096> doc;

  for (const auto& pair : reservation_dict) {
    JsonArray arr = doc.createNestedArray(String(pair.first));
    for (int val : pair.second) {
      arr.add(val);
    }
  }

  File file = LittleFS.open("/resv_tmp.json", "w");
  if (!file) {
    Serial.println("Failed to open temp file for writing.");
    return;
  }

  if (serializeJson(doc, file) == 0) {
    Serial.println("Failed to write JSON.");
    file.close();
    LittleFS.remove("/resv_tmp.json");
    return;
  }

  file.close();
  LittleFS.remove("/resv.json");
  LittleFS.rename("/resv_tmp.json", "/resv.json");
  Serial.println("Reservations saved to LittleFS.");
}

void loadReservationsFromLittleFS() {
  File file = LittleFS.open("/resv.json", "r");
  if (!file) {
    Serial.println("No reservations file found.");
    return;
  }

  StaticJsonDocument<4096> doc;
  DeserializationError err = deserializeJson(doc, file);
  file.close();

  if (err) {
    Serial.print("Failed to parse reservations: ");
    Serial.println(err.c_str());
    return;
  }

  reservation_dict.clear();

  for (JsonPair kv : doc.as<JsonObject>()) {
    int key = String(kv.key().c_str()).toInt();
    std::vector<int> values;
    for (int val : kv.value().as<JsonArray>()) {
      values.push_back(val);
    }
    reservation_dict.push_back({key, values});
  }

  Serial.println("Reservations loaded from LittleFS.");
}

// ========== RESERVATION LOGIC ==========

int addNewReservationToVector(const std::vector<int>& breads) {
  int total = vectorSum(breads);

  int new_key;

  if (reservation_keys.empty()) {
    new_key = 1;
    reservation_dict.push_back({new_key, breads});
    rebuildReservationKeys();
    dataChanged = true;
    return new_key;
  }

  int last_key = reservation_keys.back();

  std::vector<int>* last_k = getReservationByKey(last_key);
  int last_sum = (last_k != nullptr) ? vectorSum(*last_k) : 0;

  if (total == 1) {
    for (size_t i = 0; i + 1 < reservation_keys.size(); ++i) {

      std::vector<int>* s_1 = getReservationByKey(reservation_keys[i]);
      int sum1 = (s_1 != nullptr) ? vectorSum(*s_1) : 0;

      std::vector<int>* s_2 = getReservationByKey(reservation_keys[i + 1]);
      int sum2 = (s_2 != nullptr) ? vectorSum(*s_2) : 0;

      if (sum1 > 1 && sum2 > 1) {
        new_key = reservation_keys[i] + 1;
        reservation_dict.push_back({new_key, breads});
        rebuildReservationKeys();
        dataChanged = true;
        return new_key;
      }
    }

    new_key = (last_sum == 1) ? last_key + 2 : last_key + 1;
  } else {
    int last_multiple = last_key;
    for (auto it = reservation_keys.rbegin(); it != reservation_keys.rend(); ++it) {
    std::vector<int>* s_3 = getReservationByKey(*it);
    int sum = (s_3 != nullptr) ? vectorSum(*s_3) : 0;

    if (sum > 1) {
      last_multiple = *it;
      break;
    }
  }

    int distance = (last_key - last_multiple) / 2;
    if (last_multiple == last_key) {
      new_key = last_key + 2;
    } else if (distance < total && last_sum == 1) {
      new_key = last_key + 1;
    } else {
      new_key = last_multiple + (total * 2);
    }
  }

  reservation_dict.push_back({new_key, breads});
  rebuildReservationKeys();
  dataChanged = true;
  return new_key;
}

bool addNewReservationToServer(const std::vector<int>& breads, const int reservationKey) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi not connected. Reconnecting...");
    connectToWiFi();
  }

  HTTPClient http;
  String url = "http://united-kingdom.oopsididitagain.site:8000/hc/nc";
  http.begin(url);
  http.addHeader("Authorization", "Bearer " + String(token));
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Connection", "keep-alive");
  http.setTimeout(5000);

  StaticJsonDocument<512> doc;
  doc["bakery_id"] = atoi(bakery_id);
  doc["hardware_customer_id"] = reservationKey;

  JsonObject breadMap = doc.createNestedObject("bread_requirements");

  for (int i = 0; i < data_count; ++i) {
    breadMap[String(breads_id[i])] = breads[i];
  }

  String body;
  serializeJson(doc, body);

  int httpCode = http.POST(body);

  if (httpCode == 200) {
    Serial.println("Reservation sent to server.");
    http.end();
    return true;
  } else {
    Serial.printf("Failed to send reservation: %s\n", http.errorToString(httpCode).c_str());
    http.end();
    return false;
  }
}

std::pair<int, int> nextReservation() {
  if (reservation_dict.empty()) {
    Serial.println("No reservations to skip.");
    return {-1, -1}; // means there's no one to skip or serve
  }

  // Get the first key (earliest customer)
  auto it = reservation_dict.begin();
  int removed_key = it->first;

  reservation_dict.erase(it);

  int next_key = reservation_dict.empty() ? -1 : reservation_dict.begin()->first;
  rebuildReservationKeys();

  dataChanged = true;
  Serial.printf("Skipped customer #%d, next is #%d\n", removed_key, next_key);

  return {removed_key, next_key};
}

void notifyServerOfSkip(int removed_key, int next_key) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi not connected. Cannot notify server.");
    return;
  }

  HTTPClient http;
  String url = "http://united-kingdom.oopsididitagain.site:8000/hc/nt";
  http.begin(url);

  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", "Bearer " + String(token));
  http.setTimeout(5000);  // Optional timeout

  StaticJsonDocument<256> doc;
  doc["bakery_id"] = bakery_id;
  doc["current_customer_id"] = removed_key;
  doc["next_customer_id"] = next_key;

  String requestBody;
  serializeJson(doc, requestBody);

  int httpCode = http.POST(requestBody);
  if (httpCode > 0) {
    Serial.printf("Skip notification sent. HTTP Code: %d\n", httpCode);
    String response = http.getString();
    Serial.println("Server response:");
    Serial.println(response);
  } else {
    Serial.printf("Failed to send skip notification: %s\n", http.errorToString(httpCode).c_str());
  }

  http.end();
}


int compute_bread_time(const std::vector<int>& reserve) {
  int total_time = 0;
  for (size_t i = 0; i < reserve.size(); ++i) {
    int cook_time = (i < MAX_KEYS) ? bread_cook_time[i] : 1;
    total_time += reserve[i] * cook_time;
  }
  return total_time;
}

int exist_customer_time() {
  int total = 0;
  for (int key : reservation_keys) {
    std::vector<int>* res = getReservationByKey(key);
    if (res != nullptr) {
      total += compute_bread_time(*res);
    }
  }
  return total;
}

int compute_empty_slot_time() {
  int time = 0;
  int consecutive_empty = 0;
  int consecutive_full = 0;

  if (reservation_keys.empty()) return 0;

  // Use sum of first reservation as starting point
  std::vector<int>* i_1 = getReservationByKey(reservation_keys[0]);
  int prev_sum = (i_1 != nullptr) ? vectorSum(*i_1) : 0;

  for (size_t i = 1; i < reservation_keys.size(); ++i) {

    std::vector<int>* i_1 = getReservationByKey(reservation_keys[i]);
    int curr_sum = (i_1 != nullptr) ? vectorSum(*i_1) : 0;

    if (prev_sum == 1 && curr_sum == 1) {
      consecutive_empty++;
    } else {
      consecutive_empty = 0;
    }

    if (prev_sum > 1 && curr_sum > 1) {
      consecutive_full++;
    }

    prev_sum = curr_sum;
  }

  return consecutive_empty + consecutive_full;
}



// ========== FLASH PREFS ==========

void loadInitDataFromFlash() {
  if (!prefs.begin("bakery_data", true)) {
    Serial.println("Failed to open prefs!");
    return;
  }

  data_count = prefs.getInt("cnt", 0);
  if (data_count == 0 || data_count > MAX_KEYS) {
    Serial.println("No valid data in flash!");
    prefs.end();
    return;
  }

  char key[8];
  for (int i = 0; i < data_count; i++) {
    snprintf(key, sizeof(key), "k%d", i);
    breads_id[i] = prefs.getInt(key, -1);

    snprintf(key, sizeof(key), "v%d", i);
    bread_cook_time[i] = prefs.getInt(key, -1);
  }

  prefs.end();

  Serial.println("Loaded from flash:");

  for (int i = 0; i < data_count; i++) {
    Serial.printf("%d: %d → %d\n", i, breads_id[i], bread_cook_time[i]);
  }
}

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
    Serial.println("Received JSON:");
    Serial.println(payload);

    StaticJsonDocument<512> doc;
    DeserializationError error = deserializeJson(doc, payload);
    if (error) {
      Serial.print("JSON Parse Failed: ");
      Serial.println(error.c_str());
      loadInitDataFromFlash();
      return;
    }

    data_count = 0;
    for (JsonPair kv : doc.as<JsonObject>()) {
      if (data_count >= MAX_KEYS) break;
      breads_id[data_count] = String(kv.key().c_str()).toInt();
      bread_cook_time[data_count] = kv.value().as<int>();
      data_count++;
    }

    saveInitDataToFlash();
  } else {
    Serial.printf("HTTP GET failed: %s\n", http.errorToString(httpCode).c_str());
    loadInitDataFromFlash();
  }

  http.end();
}

// ========== SETUP / LOOP ==========

void setup() {
  Serial.begin(115200);
  LittleFS.begin();

  connectToWiFi();
  fetchInitData();
  loadReservationsFromLittleFS();
  rebuildReservationKeys();
}

void loop() {
  if (dataChanged && millis() - lastSaveTime > 120000) {
    saveReservationsToLittleFS();
    dataChanged = false;
    lastSaveTime = millis();
  }
}
