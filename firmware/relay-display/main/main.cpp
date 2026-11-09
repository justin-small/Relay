// The Relay panel's boot. The order is the order of the hardware:
// the panel is kept dark first, then the configuration is read, then the panel is
// brought up and shows the bench screen, and only then does the network decide what it
// is: a stored network is joined, and with none the setup network opens (`provision.cpp`).

#include "button.h"
#include "captions.h"
#include "config.h"
#include "diag.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "display.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_timer.h"
#include "net.h"
#include "nvs_flash.h"
#include "provision.h"
#include "screen.h"
#include "settings.h"
#include "touch.h"

namespace {

const char *TAG = "boot";

// NVS is the first thing that can be wrong on a board that has been used. Two
// errors mean the partition has to be erased before it is usable: no free pages,
// and a version the running code does not know, which is what a downgrade finds.
//
// **Erasing on a downgrade loses the stored configuration.** The alternative is to
// refuse to boot, which is worse on a wall bracket. The loss is one provisioning
// visit, and the warning is written so the cause is in the log.
esp_err_t nvs_start()
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "nvs: partition needs erasing (%s); erasing it", esp_err_to_name(err));
        ESP_RETURN_ON_ERROR(nvs_flash_erase(), TAG, "nvs erase");
        err = nvs_flash_init();
    }
    return err;
}

// The heap, every thirty seconds. The soak test reads these lines to see whether a long run keeps
// its memory. `low` is the least free internal heap since boot: it can only fall, so a leak shows as it
// falling over the run, where a free figure sampled during a TLS connection would not show it. `largest`
// falls while `free` holds when the heap fragments. Logged in one line so a log search finds it.
void heap_log(void *)
{
    ESP_LOGI(TAG, "heap: free internal %u low %u largest %u psram %u",
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
             static_cast<unsigned>(heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL)),
             static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL)),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
}

void start_heap_log()
{
    constexpr uint64_t kHeapLogUs = 30ULL * 1000 * 1000;
    static esp_timer_handle_t timer = nullptr;
    esp_timer_create_args_t args = {};
    args.callback = heap_log;
    args.name = "heap_log";
    ESP_ERROR_CHECK(esp_timer_create(&args, &timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(timer, kHeapLogUs));
}

}  // namespace

// **The panel is set up from core 1, so its interrupt lands there.** The RGB driver allocates its
// interrupt on the core that installs it, and app_main runs on core 0, which is Wi-Fi's core. The bounce buffer is
// refilled in that interrupt, and any critical section on its core delays the refill: a FreeRTOS task snapshot
// every five seconds flickered the panel on every one, and a phone busy on the network did the same through
// Wi-Fi's own. A critical section masks only its own core, so on core 1 the refill no longer waits for core 0.
namespace {

struct PanelInit {
    int rotation_deg;
    esp_err_t result;
    TaskHandle_t caller;
};

void panel_init_task(void *arg)
{
    auto *p = static_cast<PanelInit *>(arg);
    p->result = display_init(p->rotation_deg);
    xTaskNotifyGive(p->caller);
    vTaskDelete(nullptr);
}

esp_err_t display_init_on_core1(int rotation_deg)
{
    PanelInit p = {rotation_deg, ESP_FAIL, xTaskGetCurrentTaskHandle()};
    if (xTaskCreatePinnedToCore(panel_init_task, "panel_init", 4096, &p, 5, nullptr, 1) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    return p.result;
}

}  // namespace

extern "C" void app_main(void)
{
    // First, before any other work. The STC8 is sent the backlight's off command so the
    // panel is dark for everything below. A board without the STC8 boots with the screen dark.
    ESP_ERROR_CHECK(display_backlight_init());
    ESP_LOGI(TAG, "boot: relay-display %s, %s", RELAY_FW_VERSION, RELAY_BOARD_NAME);
    ESP_LOGI(TAG, "boot: psram %u bytes", static_cast<unsigned>(esp_psram_get_size()));

    ESP_ERROR_CHECK(nvs_start());

    relay_settings_t settings = {};
    ESP_ERROR_CHECK(settings_load(&settings));
    // The panel's name to Relay: made once, on first boot, and kept until a factory reset.
    bool made_id = false;
    ESP_ERROR_CHECK(settings_ensure_panel_id(&settings, &made_id));

    // A panel that will not initialise leaves nothing to show, so that is fatal and
    // the reset is the ESP-IDF default. A test screen that does not finish is not:
    // the network still starts, and the panel reports it.
    ESP_ERROR_CHECK(display_init_on_core1(settings.rotation_deg));
    const esp_err_t screen = display_show_test_screen(RELAY_FW_VERSION);
    if (screen != ESP_OK) {
        ESP_LOGE(TAG, "boot: test screen did not reach the panel (%s); continuing", esp_err_to_name(screen));
    }
    // LVGL draws everything after the bench picture. It starts before the network, so the panel can show its
    // screens as the network comes up, and it draws nothing until something is shown.
    ESP_ERROR_CHECK(screen_start());
    // Touch is LVGL's pointer. A controller that does not answer costs the touch and not the boot: captions need none.
    const esp_err_t touch = touch_start(settings.rotation_deg);
    if (touch != ESP_OK) {
        ESP_LOGE(TAG, "boot: touch not started (%s); the screen works without it", esp_err_to_name(touch));
    }

    // The network: a stored Wi-Fi network is joined, otherwise the setup network opens and
    // the panel shows its passphrase. Either way `recover` on the serial console is there.
    ESP_ERROR_CHECK(provision_start(settings));

    // The BOOT button holding it for five seconds opens the setup network, as `recover` does. It starts
    // after `provision_start`, so the function it calls is ready.
    // **A configured board may open its setup network here because a person is holding the button.** The I/O board's
    // rule is that a configured board never opens an access point unattended. This is not unattended: the press is the
    // same act as typing `recover`, at the device. A stored network that fails is still only retried in the background
    // (`provision.cpp`). A button that cannot be configured costs the button and not the boot, so the error is logged.
    const esp_err_t button = button_start();
    if (button != ESP_OK) {
        ESP_LOGE(TAG, "boot: BOOT button not started (%s); recover is still on the serial console", esp_err_to_name(button));
    }

    // The emulator's wired link. A no-op on the released board, which has none.
    ESP_ERROR_CHECK(net_wired_start());

    // The caption feed. It waits for an address by itself, so it starts here whatever the network is doing.
    // The stored Relay address and feed choose what slot 0 shows; with either missing it stays off until the
    // feed is set (Relay discovery and the feed picker come with later changes). A feed that cannot start costs
    // the captions and not the boot.
    const esp_err_t captions = captions_start();
    if (captions != ESP_OK) {
        ESP_LOGE(TAG, "boot: captions not started (%s)", esp_err_to_name(captions));
    } else if (settings.relay_addr[0] != '\0' && settings.feed_stream != kFeedNone) {
        captions_set_feed(0, settings.relay_addr, settings.feed_stream, settings.feed_lang);
        screen_show_captions(0);
    }
    start_heap_log();
    diag_start();
    ESP_LOGI(TAG, "boot: ready");
}
