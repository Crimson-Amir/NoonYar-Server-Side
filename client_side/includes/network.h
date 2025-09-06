#ifndef NETWORK_H
#define NETWORK_H

#include <WiFi.h>
#include <PubSubClient.h>
#include <HTTPClient.h>
#include "config.h"
#include "types.h"

// ---------- GLOBAL NETWORK OBJECTS ----------
extern WiFiClient net;
extern PubSubClient mqtt;
extern String topic_errors;
extern String topic_bread_time;

// ---------- NETWORK STATE MANAGEMENT ----------
extern SemaphoreHandle_t networkBlockMutex;
extern bool networkBlock;
extern unsigned long lastWifiAttempt;
extern unsigned long lastMqttAttempt;

void setNetworkBlock(bool enable);
bool isNetworkBlocked();
bool isNetworkReadyForApi();
bool isNetworkReady();
void ensureConnectivity();

// ---------- HTTP FUNCTIONS ----------
String sendHttpRequest(const String& url, const char* method, const String& body = "", uint16_t timeoutMs = HTTP_TIMEOUT);

#endif
