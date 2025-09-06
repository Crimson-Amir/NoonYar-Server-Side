#include "display.h"

// ---------- DISPLAY OBJECTS ----------
LedControl lc(DIN_PIN, CLK_PIN, CS_PIN, 1);
volatile DeviceStatus currentStatus = STATUS_NORMAL;
int num1 = 0;
int num2 = 0;
int num3 = 0;

void displayChar(char c) {
  lc.clearDisplay(0);
  lc.setChar(0, 0, c, false);
}

void displayDash() {
  lc.clearDisplay(0);
  lc.setChar(0, 0, '-', false);
}

void showNumbers(int a, int b, int c) {
  if (currentStatus == STATUS_NORMAL) {
    lc.clearDisplay(0);
    lc.setDigit(0, 0, a % 10, false);
    lc.setDigit(0, 1, b % 10, false);
    lc.setDigit(0, 2, c % 10, false);
  }
}

void setStatus(DeviceStatus st) {
  currentStatus = st;
  lc.clearDisplay(0);

  if (st == STATUS_NORMAL) {
    showNumbers(num1, num2, num3);
  }
  else if (st == STATUS_WIFI_ERROR) {
    displayChar('W');
  }
  else if (st == STATUS_MQTT_ERROR) {
    displayChar('M');
  }
  else if (st == STATUS_API_ERROR) {
    displayChar('E');
  }
  else if (st == STATUS_API_WAITING) {
    displayDash();
  }
}
