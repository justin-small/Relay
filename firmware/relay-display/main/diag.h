#pragma once

// Development diagnostics. Everything here is compiled to nothing unless
// CONFIG_RELAY_DEV_DIAG is on, which only `sdkconfig.dev` does.

#include <stdint.h>

#include "sdkconfig.h"

#if CONFIG_RELAY_DEV_DIAG
// Starts the five-second task: heap, and FreeRTOS's CPU share per task.
void diag_start();
// The five-second block's mode from the serial console: on, off, quiet, print, tasks or wifi.
// False for a name it does not know.
bool diag_set_mode(const char *name);
// Milliseconds since `since_us`, for a phase's log line.
int64_t diag_ms_since(int64_t since_us);
// One LVGL flush's duration in microseconds (room_screen.cpp), summed into the five-second line.
void diag_note_flush(int64_t us);
#define DIAG_NOW() esp_timer_get_time()
#else
inline void diag_start() {}
#define DIAG_NOW() 0
#endif
