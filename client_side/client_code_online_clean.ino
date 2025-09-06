#include <LittleFS.h>
#include "includes/config.h"
#include "includes/types.h"
#include "includes/network.h"
#include "includes/mqtt.h"
#include "includes/mutex.h"
#include "includes/display.h"
#include "includes/api.h"
#include "includes/tasks.h"


void setup() {
  // Filesystem
  LittleFS.begin();

  // Display init
  lc.shutdown(0, false);
  lc.setIntensity(0, 8);
  lc.clearDisplay(0);

  // Mutex initialization
  busyMutex = xSemaphoreCreateMutex();
  mqttQueueMutex = xSemaphoreCreateMutex();
  networkBlockMutex = xSemaphoreCreateMutex();

  // WiFi initialization
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  // MQTT initialization
  mqtt.setServer(mqtt_server, mqtt_port);
  mqtt.setCallback(mqttCallback);

  // Start tasks
  xTaskCreatePinnedToCore(mqttPublisherTask, "MqttPublisher", 4096, NULL, 2, NULL, 0);
  xTaskCreatePinnedToCore(fetchInitTask, "InitFetchBoot", 4096, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(ticketFlowTask, "TicketFlow", 8192, NULL, 1, NULL, 1);

  showNumbers(num1, num2, num3);
}

void loop() {
  ensureConnectivity();
  checkDeadlock();
}
