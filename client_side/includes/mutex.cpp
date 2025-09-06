#include "mutex.h"
#include "mqtt.h"
#include "config.h"

// ---------- MUTEX MANAGEMENT ----------
SemaphoreHandle_t busyMutex;
unsigned long busyLockedAt = 0;

bool tryLockBusy(uint32_t timeoutMs) {
    if (xSemaphoreTake(busyMutex, timeoutMs / portTICK_PERIOD_MS) == pdTRUE) {
        busyLockedAt = millis();
        return true;
    }
    return false;
}

void unlockBusy() {
    busyLockedAt = 0;
    xSemaphoreGive(busyMutex);
}

void checkDeadlock() {
    if (busyLockedAt > 0 && millis() - busyLockedAt > DEADLOCK_TIMEOUT) {
        mqttPublishError("deadlock_detected_rebooting");
        ESP.restart();
    }
}

bool isBusyNow() {
    if (xSemaphoreTake(busyMutex, (TickType_t)0) == pdTRUE) {
        xSemaphoreGive(busyMutex);
        return false;
    }
    return true;
}
