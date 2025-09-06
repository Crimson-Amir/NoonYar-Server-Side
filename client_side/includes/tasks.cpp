#include "tasks.h"
#include "api.h"
#include "mqtt.h"
#include "mutex.h"
#include "display.h"
#include "network.h"
#include "config.h"

void fetchInitTask(void* param) {
    init_success = false;
    setStatus(STATUS_API_WAITING);
    while (!fetchInitData()) {
        mqttPublishError("init_retrying");
        vTaskDelay(INIT_RETRY_DELAY / portTICK_PERIOD_MS);
    }
    setStatus(STATUS_NORMAL);
    init_success = true;
    vTaskDelete(NULL);
}

void newCustomerTask(void* param) {
  int bread_count = *(int*)param;
  delete (int*)param;

  if (!isNetworkReadyForApi()) {
    mqttPublishError("nc:network_not_ready");
    vTaskDelete(NULL);
  }

  if (!tryLockBusy()) {
    mqttPublishError("busy:new_customer");
    vTaskDelete(NULL);
  }

  setStatus(STATUS_API_WAITING);
  
  std::vector<int> breads(bread_buffer, bread_buffer + bread_count);
  int cid = apiNewCustomer(breads);
  
  setStatus(cid != -1 ? STATUS_NORMAL : STATUS_API_ERROR);
  if (cid == -1) mqttPublishError("nc:failed");
  hasCustomerInQueue = true;
  unlockBusy();
  vTaskDelete(NULL);
}

void nextTicketTask(void* param) {
  int ticketId = *(int*)param;
  delete (int*)param;

  if (!isNetworkReadyForApi()) {
    mqttPublishError("nt:network_not_ready");
    vTaskDelete(NULL);
  }

  if (!tryLockBusy()) {
    vTaskDelete(NULL);
  }

  setStatus(STATUS_API_WAITING);
  NextTicketResponse r = apiNextTicket(ticketId);
  bool ok = (r.current_ticket_id != -1);
  setStatus(ok ? STATUS_NORMAL : STATUS_API_ERROR);
  if (!ok) mqttPublishError(String("nt:failed:") + r.error);

  unlockBusy();
  vTaskDelete(NULL);
}

void currentTicketTask(void* param) {
  if (!isNetworkReadyForApi()) {
    mqttPublishError("ct:network_not_ready");
    vTaskDelete(NULL);
  }

  CurrentTicketResponse r = apiCurrentTicket();
  bool ok = (r.current_ticket_id != -1);
  if (!ok) mqttPublishError(String("ct:failed:") + r.error);

  vTaskDelete(NULL);
}

void skipTicketTask(void* param) {
  int ticketId = *(int*)param;
  delete (int*)param;

  if (!isNetworkReadyForApi()) {
    mqttPublishError("st:network_not_ready");
    vTaskDelete(NULL);
  }

  bool ok = apiSkipTicket(ticketId);
  if (!ok) mqttPublishError("st:failed");

  vTaskDelete(NULL);
}

int calculateCookTime(const CurrentTicketResponse& cur) {
  int totalTime = 0;
  for (int i = 0; i < cur.bread_count; i++) {
    int breadId = cur.breads[i];
    int count   = cur.bread_counts[i];
    for (int j = 0; j < data_count; j++) {
      if (breads_id[j] == breadId) {
        totalTime += count * bread_cook_time[j];
        break;
      }
    }
  }
  return totalTime;
}

void ticketFlowTask(void* param) {
  unsigned long lastCheckTime = 0;
  const unsigned long checkInterval = 300000UL; // 5 minutes

  while (true) {
    unsigned long now = millis();

    // only skip if no queue AND interval hasn't expired
    if (!hasCustomerInQueue && (now - lastCheckTime < checkInterval)) {
      vTaskDelay(5000 / portTICK_PERIOD_MS);
      continue;
    }

    CurrentTicketResponse cur = apiCurrentTicket();
    lastCheckTime = now;

    if (!cur.error.isEmpty() || cur.current_ticket_id < 0) {
      hasCustomerInQueue = false; // still none in queue
      vTaskDelay(5000 / portTICK_PERIOD_MS);
      continue;
    }

    hasCustomerInQueue = true;
    int ticketId = cur.current_ticket_id;
    announceTicket(ticketId);

    // 2. Cook time (seconds)
    int cookTimeSeconds = calculateCookTime(cur);
    announceNextTicketReadyIn(cookTimeSeconds);
    unsigned long deadline = millis() + (cookTimeSeconds * 1000UL);

    // 3. Wait for scan or timeout
    bool processed = false;
    while (!processed) {
      if (ticketScannedId == ticketId) {
        ticketScannedId = -1;
        NextTicketResponse resp = apiNextTicket(ticketId);
        showBreadsOnDisplay(resp);
        vTaskDelay(60000 / portTICK_PERIOD_MS); // +1 minute wait
        processed = true;
      }

      if (millis() >= deadline) {
        apiSkipTicket(ticketId);
        processed = true;
      }

      vTaskDelay(1000 / portTICK_PERIOD_MS);
    }
  }
}