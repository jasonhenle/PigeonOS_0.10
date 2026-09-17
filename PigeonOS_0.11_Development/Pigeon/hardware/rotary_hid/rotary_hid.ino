/*
  Pigeon rotary encoder → host navigation

  Intended map (matches pigeon_0_9.py hotkeys):
    CW   → forward  (Right / serial RIGHT)
    CCW  → backward (Left  / serial LEFT)
    PUSH → activate (Space / serial PUSH)

  Two transport modes (picked at compile time):

  1) USB HID Keyboard — Leonardo, Pro Micro, Pico, ESP32-S2/S3, …
     Emits real Left / Right / Space. No host serial code needed.

  2) USB Serial line protocol — Arduino UNO Q and other non-HID boards.
     Emits lines: RIGHT / LEFT / PUSH (+ PIGEON_CONTROLLER_READY).
     Host: pigeon.rotary_serial (USB CDC and/or UNO Q Monitor TCP :7500).

  *** Arduino UNO Q ***
  On UNO Q, ``Serial`` is UART D0/D1 (not USB-C).
  USB / App Lab / IDE Serial Monitor use ``Monitor`` via Arduino_RouterBridge.
  You MUST call Bridge.begin() before Monitor.begin() — otherwise lines never
  leave the MCU. The host (Pi) may read them via:
    - USB CDC virtual COM (when the board enumerates one), or
    - ADB forward of the Monitor TCP port (localhost:7500) — preferred.

  Arduino UNO Q ("Arduino Q"):
    Tools → Board → Arduino UNO Q
    Library Manager: **Arduino_RouterBridge** (+ deps)
    Pins: UNO-header D2/D3/D4

  Default wiring (KY-040-style module, shared GND / 5V or 3V3):
    CLK / A  → pin 2
    DT  / B  → pin 3
    SW       → pin 4  (INPUT_PULLUP)

  Force serial mode on an HID-capable board:  -DPIGEON_FORCE_SERIAL
  Force HID when detection is wrong:          -DPIGEON_FORCE_HID
*/

// --- Transport selection ----------------------------------------------------
#if defined(PIGEON_FORCE_SERIAL)
#define PIGEON_USE_SERIAL 1
#elif defined(PIGEON_FORCE_HID)
#define PIGEON_USE_SERIAL 0
#elif defined(ARDUINO_ARCH_ESP32)
#define PIGEON_USE_SERIAL 0
#elif defined(USBCON) || defined(ARDUINO_ARCH_RP2040) || defined(ARDUINO_ARCH_SAMD) || defined(ARDUINO_ARCH_MBED)
#define PIGEON_USE_SERIAL 0
#else
// UNO Q (Zephyr), classic Uno/Nano/Mega UART USB, etc.
#define PIGEON_USE_SERIAL 1
#endif

#if !PIGEON_USE_SERIAL
#if defined(ARDUINO_ARCH_ESP32)
#include "USB.h"
#include "USBHIDKeyboard.h"
static USBHIDKeyboard Keyboard;
#ifndef KEY_LEFT_ARROW
#define KEY_LEFT_ARROW 0xD8
#endif
#ifndef KEY_RIGHT_ARROW
#define KEY_RIGHT_ARROW 0xD7
#endif
#else
#include <Keyboard.h>
#endif
#endif

#if PIGEON_USE_SERIAL
#if __has_include(<Arduino_RouterBridge.h>)
#include <Arduino_RouterBridge.h>
#define PIGEON_USE_MONITOR 1
#else
#define PIGEON_USE_MONITOR 0
#endif
#endif

// --- Pins (change to match your wiring) ---
static const uint8_t PIN_CLK = 2;
static const uint8_t PIN_DT = 3;
static const uint8_t PIN_SW = 4;

static const unsigned long ENCODER_MIN_MS = 8;
static const unsigned long TURN_COOLDOWN_MS = 40;
static const unsigned long BUTTON_DEBOUNCE_MS = 30;
static const unsigned long ACTIVATE_MIN_MS = 150;

static uint8_t lastClk = HIGH;
static unsigned long lastEdgeMs = 0;
static unsigned long lastTurnMs = 0;
static unsigned long lastActivateMs = 0;

static uint8_t lastSwRaw = HIGH;
static uint8_t swStable = HIGH;
static unsigned long swChangeMs = 0;

#if !PIGEON_USE_SERIAL
static void tapKey(uint8_t key) {
  Keyboard.press(key);
  delay(8);
  Keyboard.release(key);
}
#endif

#if PIGEON_USE_SERIAL
static void hostPrintln(const __FlashStringHelper *line) {
#if PIGEON_USE_MONITOR
  Monitor.println(line);
#endif
  // Also print on UART Serial (D0/D1) for USB-TTL adapters / debugging.
  Serial.println(line);
}
#endif

static void emitForward() {
#if PIGEON_USE_SERIAL
  hostPrintln(F("RIGHT"));
#else
  tapKey(KEY_RIGHT_ARROW);
#endif
}

static void emitBackward() {
#if PIGEON_USE_SERIAL
  hostPrintln(F("LEFT"));
#else
  tapKey(KEY_LEFT_ARROW);
#endif
}

static void emitActivate() {
#if PIGEON_USE_SERIAL
  hostPrintln(F("PUSH"));
#else
  tapKey(' ');
#endif
}

void setup() {
  pinMode(PIN_CLK, INPUT_PULLUP);
  pinMode(PIN_DT, INPUT_PULLUP);
  pinMode(PIN_SW, INPUT_PULLUP);
  lastClk = digitalRead(PIN_CLK);
  lastSwRaw = digitalRead(PIN_SW);
  swStable = lastSwRaw;

#if PIGEON_USE_SERIAL
#if PIGEON_USE_MONITOR
  // Required on UNO Q — without Bridge, Monitor.println never leaves the MCU.
  Bridge.begin();
  Monitor.begin();
#endif
  Serial.begin(115200);
  delay(200);
  hostPrintln(F("PIGEON_CONTROLLER_READY"));
  delay(100);
  hostPrintln(F("PIGEON_CONTROLLER_READY"));
#else
#if defined(ARDUINO_ARCH_ESP32)
  USB.begin();
#endif
  Keyboard.begin();
#endif
}

void loop() {
  const unsigned long now = millis();

  const uint8_t clk = digitalRead(PIN_CLK);
  if (clk != lastClk) {
    if (clk == LOW && (now - lastEdgeMs) >= ENCODER_MIN_MS) {
      lastEdgeMs = now;
      if ((now - lastTurnMs) >= TURN_COOLDOWN_MS) {
        lastTurnMs = now;
        if (digitalRead(PIN_DT) == HIGH) {
          emitForward();
        } else {
          emitBackward();
        }
      }
    }
    lastClk = clk;
  }

  const uint8_t sw = digitalRead(PIN_SW);
  if (sw != lastSwRaw) {
    lastSwRaw = sw;
    swChangeMs = now;
  }
  if ((now - swChangeMs) >= BUTTON_DEBOUNCE_MS && sw != swStable) {
    swStable = sw;
    if (swStable == LOW && (now - lastActivateMs) >= ACTIVATE_MIN_MS) {
      lastActivateMs = now;
      emitActivate();
    }
  }
}
