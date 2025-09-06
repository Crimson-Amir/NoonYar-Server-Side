#include "mqtt.h"
#include "network.h"
#include "api.h"
#include "mutex.h"

// ---------- MQTT QUEUE MANAGEMENT ----------
SemaphoreHandle_t mqttQueueMutex;
std::deque<MqttMessage> mqttMessageQueue;

bool queueMqttMessage(const String& topic, const String& payload, bool retain) {
  if (xSemaphoreTake(mqttQueueMutex, MQTT_QUEUE_TIMEOUT / portTICK_PERIOD_MS) == pdTRUE) {
    if (mqttMessageQueue.size() < MAX_MQTT_QUEUE_SIZE) {
      MqttMessage msg;
      msg.topic = topic;
      msg.payload = payload;
      msg.retain = retain;
      mqttMessageQueue.push_back(msg);
      xSemaphoreGive(mqttQueueMutex);
      return true;
    }
    xSemaphoreGive(mqttQueueMutex);
  }
  return false;
}

void mqttPublishError(const String& msg) {
  queueMqttMessage(topic_errors, msg, true);
}

void mqttPublish(const String& topic, const String& payload, bool retain) {
  queueMqttMessage(topic, payload, retain);
}

void mqttPublishBreadTime(const String& payload) {
  queueMqttMessage(topic_bread_time, payload, false);
}

int getMqttQueueSize() {
  int size = 0;
  if (xSemaphoreTake(mqttQueueMutex, 100 / portTICK_PERIOD_MS) == pdTRUE) {
    size = mqttMessageQueue.size();
    xSemaphoreGive(mqttQueueMutex);
  }
  return size;
}

void mqttPublisherTask(void* param) {
  unsigned long lastQueueCheck = 0;
  int queueOverflowCount = 0;
  
  while (true) {
    if (mqtt.connected()) {
      if (xSemaphoreTake(mqttQueueMutex, 100 / portTICK_PERIOD_MS) == pdTRUE) {
        if (!mqttMessageQueue.empty()) {
          MqttMessage msg = mqttMessageQueue.front();
          mqttMessageQueue.pop_front();
          xSemaphoreGive(mqttQueueMutex);
          
          bool published = mqtt.publish(msg.topic.c_str(), msg.payload.c_str(), msg.retain);
          if (!published) {
            if (!mqtt.connected()) {
              Serial.println("MQTT disconnected during publish");
            } else {
              Serial.println("MQTT publish failed: " + msg.topic + " -> " + msg.payload);
            }
          }
        } else {
          xSemaphoreGive(mqttQueueMutex);
        }
        
        if (millis() - lastQueueCheck > 5000) {
          lastQueueCheck = millis();
          int queueSize = mqttMessageQueue.size();
          if (queueSize > MAX_MQTT_QUEUE_SIZE * 0.8) {
            queueOverflowCount++;
            if (queueOverflowCount > 3) {
              int clearCount = queueSize / 2;
              for (int i = 0; i < clearCount; i++) {
                mqttMessageQueue.pop_front();
              }
              queueOverflowCount = 0;
              Serial.println("MQTT queue overflow - cleared " + String(clearCount) + " messages");
            }
          } else {
            queueOverflowCount = 0;
          }
        }
      }
    }
    vTaskDelay(50 / portTICK_PERIOD_MS);
  }
}

void fetchInitFromMqttTask(void* param) {
    if (!isNetworkReadyForApi()) {
        mqttPublishError("init:network_not_ready");
        vTaskDelete(NULL);
    }

    if (!tryLockBusy()) {
        mqttPublishError("❌ Device busy");
        vTaskDelete(NULL);
    }

    bool ok = fetchInitData();
    if (!ok) {
        mqttPublishError("failed");
    }
    
    unlockBusy();
    vTaskDelete(NULL);
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
    if (String(topic) == topic_bread_time) {
        xTaskCreatePinnedToCore(
            fetchInitFromMqttTask,
            "FetchInitOnMqtt", 4096, NULL, 1, NULL, 1
        );
    }
}
