// The BOOT button as the way to open the setup network. Holding it for five seconds does what
// `recover` does on the serial console: the setup network opens with a new passphrase, and a stored
// network is kept until a new one succeeds. `button_policy.h` decides when a press counts.
//
// **A configured board still never opens its setup network unattended.** A stored network that fails
// is retried in the background and nothing else happens (`provision.cpp`). A held button is a person at
// the device, and it is the only trigger here that does not come from a timer or from the network.

#include "button.h"

#include "button_policy.h"
#include "config.h"
#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "provision.h"
#include "sdkconfig.h"
#include "settings.h"

namespace {

// Unused under the emulator, which compiles the task and the pin set-up out (`button_start`).
[[maybe_unused]] const char *TAG = "button";

// Sampled every 20 ms, so the 50 ms debounce is about three samples and the five-second hold is
// timed to within one sample. The alternative was an interrupt on the pin, which would have to
// debounce in the ISR or on a timer anyway, and would wake the task for every bounce.
constexpr int kSamplePeriodMs = 20;

// The same call the console makes, from a task with the same 4 KB stack, so the setup network's
// start-up has the room it already has (`provision.cpp`).
constexpr uint32_t kStackBytes = 4096;

// Below the serial console (3). Nothing waits on this task, and a press is a person's, not a deadline.
constexpr UBaseType_t kPriority = 2;

// Compiled out under the emulator with the task that calls it: `button_start` never starts the task there, and
// an unused function is a warning this build treats as a failure.
#if !CONFIG_RELAY_QEMU
void button_task(void *)
{
    button::Hold hold;
    for (;;) {
        // Active low: the pin reads 0 while the button is down.
        const bool pressed = gpio_get_level(static_cast<gpio_num_t>(kPinBoot)) == 0;
        const button::Event event = hold.sample(esp_timer_get_time() / 1000, pressed);
        if (event == button::Event::recover) {
            ESP_LOGW(TAG, "BOOT held 5 s: opening the setup network (hold to 15 s to erase everything)");
            // The same call as `recover`, from another task. `provision_open_setup` serialises the two
            // with its own lock, so a press and a console line at once open the network once.
            provision_open_setup(true);
        } else if (event == button::Event::factory_reset) {
            ESP_LOGW(TAG, "BOOT held 15 s: factory reset");
            const esp_err_t err = settings_factory_reset();
            if (err != ESP_OK) {
                // Restarting anyway would boot on whatever half of the storage survived, which is the state a
                // person holding the button for fifteen seconds asked to be rid of; the log says it failed.
                ESP_LOGE(TAG, "factory reset failed (%s); not restarting", esp_err_to_name(err));
            } else {
                esp_restart();
            }
        }
        vTaskDelay(pdMS_TO_TICKS(kSamplePeriodMs));
    }
}
#endif

}  // namespace

esp_err_t button_start()
{
#if CONFIG_RELAY_QEMU
    // **The emulator does not start this task.** Its virtual board has no button, and an unconnected
    // GPIO0 reads low. A task here would hold the button down from boot, open the setup network in
    // every emulator run, and leave the tests with a network they did not ask for.
    return ESP_OK;
#else
    gpio_config_t cfg = {};
    cfg.pin_bit_mask = 1ULL << kPinBoot;
    cfg.mode = GPIO_MODE_INPUT;
    // The board's own pull-up on this net is not documented (`config.h`). The internal one holds the level
    // high while the button is up. It is harmless beside a pull-up the board also has.
    cfg.pull_up_en = GPIO_PULLUP_ENABLE;
    cfg.pull_down_en = GPIO_PULLDOWN_DISABLE;
    cfg.intr_type = GPIO_INTR_DISABLE;
    ESP_RETURN_ON_ERROR(gpio_config(&cfg), TAG, "BOOT pin");
    if (xTaskCreate(button_task, "button", kStackBytes, nullptr, kPriority, nullptr) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
#endif
}
