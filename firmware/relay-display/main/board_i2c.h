#pragma once

#include <cstdint>

#include "driver/i2c_master.h"
#include "esp_err.h"

// The board's one I2C bus, shared by the STC8 that switches the backlight, the
// RTC that `rtc.cpp` reads at boot and the touch controller this target does not use. The bus
// is created once and every caller gets the same controller, because two buses on the
// same pins would fight each other.

// Start the bus the first time, and return the same handle after that. Returns nullptr
// when the controller cannot be started, and logs why. Called from `app_main` before
// any task that uses the bus starts, so there is no race on the first call.
i2c_master_bus_handle_t board_i2c_init();

// Add the device at the 7-bit `address` to the bus, at the board's speed. The handle is
// the caller's to keep. Adding a device does not probe it: a device that is not there
// is found by the first transfer to it.
esp_err_t board_i2c_add_device(uint8_t address, i2c_master_dev_handle_t *out);
