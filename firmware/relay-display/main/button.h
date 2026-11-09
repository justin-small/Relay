#pragma once

#include "esp_err.h"

// The BOOT button as a way to open the setup network. Holding it for five seconds calls the
// same function the serial console's `recover` calls (`provision_open_setup(true)`). Held on to fifteen
// seconds it is a factory reset, `settings_factory_reset` and a restart. `button_policy.h`
// decides when a press counts.

// Configure the BOOT pin and start its sampling task. Called once from `app_main`, after
// `provision_start`, so `provision_open_setup` is ready before the button can call it. The emulator
// builds do not start the task (`button.cpp`).
esp_err_t button_start();
