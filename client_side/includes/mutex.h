#ifndef MUTEX_H
#define MUTEX_H

#include <Arduino.h>

// ---------- MUTEX MANAGEMENT ----------
extern SemaphoreHandle_t busyMutex;
extern unsigned long busyLockedAt;

bool tryLockBusy(uint32_t timeoutMs = BUSY_TIMEOUT);
void unlockBusy();
void checkDeadlock();
bool isBusyNow();

#endif
