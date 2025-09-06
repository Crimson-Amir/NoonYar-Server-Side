#ifndef DISPLAY_H
#define DISPLAY_H

#include <LedControl.h>
#include "types.h"
#include "config.h"

// ---------- DISPLAY OBJECTS ----------
extern LedControl lc;
extern volatile DeviceStatus currentStatus;
extern int num1, num2, num3;

// ---------- DISPLAY FUNCTIONS ----------
void displayChar(char c);
void displayDash();
void showNumbers(int a, int b, int c);
void setStatus(DeviceStatus st);

#endif
