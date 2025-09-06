#ifndef MQTT_H
#define MQTT_H

#include "types.h"
#include "config.h"

// ---------- MQTT QUEUE MANAGEMENT ----------
extern SemaphoreHandle_t mqttQueueMutex;
extern std::deque<MqttMessage> mqttMessageQueue;

// ---------- MQTT FUNCTIONS ----------
bool queueMqttMessage(const String& topic, const String& payload, bool retain = false);
void mqttPublishError(const String& msg);
void mqttPublish(const String& topic, const String& payload, bool retain = false);
void mqttPublishBreadTime(const String& payload);
int getMqttQueueSize();

// ---------- MQTT TASKS ----------
void mqttPublisherTask(void* param);
void fetchInitFromMqttTask(void* param);
void mqttCallback(char* topic, byte* payload, unsigned int length);

#endif
