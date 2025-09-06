#ifndef TYPES_H
#define TYPES_H

#include <vector>
#include <deque>
#include <Arduino.h>

// ---------- DISPLAY STATE ----------
enum DeviceStatus : uint8_t {
  STATUS_NORMAL,
  STATUS_WIFI_ERROR,
  STATUS_MQTT_ERROR,
  STATUS_API_WAITING,
  STATUS_API_ERROR
};

// ---------- MQTT MESSAGE STRUCTURE ----------
struct MqttMessage {
  String topic;
  String payload;
  bool retain;
};

// ---------- API RESPONSE STRUCTURES ----------
struct NextTicketResponse {
  int current_ticket_id = -1;
  bool skipped_customer = false;
  int breads[MAX_KEYS];
  int bread_counts[MAX_KEYS];
  int bread_count = 0;
  String error;
};

struct CurrentTicketResponse {
  int current_ticket_id = -1;
  int breads[MAX_KEYS];
  int bread_counts[MAX_KEYS];
  int bread_count = 0;
  String error;
};

#endif
