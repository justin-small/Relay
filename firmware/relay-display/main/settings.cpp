#include "settings.h"

#include <cstring>

#include "config.h"
#include "display.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

namespace {

const char *TAG = "settings";

// The NVS namespace and key. The namespace is short because NVS limits both names
// to 15 characters.
constexpr const char *kNamespace = "relay";
constexpr const char *kKey = "config";

// The orientation this build was configured for. It is the defaults' rotation, and it
// is also what a stored rotation that is neither 0 nor 180 falls back to. The Kconfig
// value is 0 or 180, so the cast loses nothing.
uint16_t configured_rotation()
{
    return static_cast<uint16_t>(CONFIG_RELAY_ROTATION_DEG);
}

}  // namespace

void settings_defaults(relay_settings_t *out)
{
    settings_defaults_fill(out, configured_rotation());
}

esp_err_t settings_load(relay_settings_t *out)
{
    settings_defaults(out);

    nvs_handle_t handle = 0;
    esp_err_t err = nvs_open(kNamespace, NVS_READONLY, &handle);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        // A board that has never been configured. Defaults are the normal case.
        ESP_LOGI(TAG, "settings: none stored, using defaults");
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(err, TAG, "open");

    relay_settings_t stored = {};
    size_t len = sizeof stored;
    err = nvs_get_blob(handle, kKey, &stored, &len);
    nvs_close(handle);

    if (err == ESP_ERR_NVS_NOT_FOUND) {
        ESP_LOGI(TAG, "settings: none stored, using defaults");
        return ESP_OK;
    }
    // A blob that will not read is not a reason to stop the panel. Defaults show
    // the test screen and stay off the network; the warning says why.
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "settings: stored blob unreadable (%s, %u bytes), using defaults",
                 esp_err_to_name(err), static_cast<unsigned>(len));
        return ESP_OK;
    }

    switch (settings_decode(&stored, len, out, configured_rotation())) {
    case settings_blob::wrong_size:
        ESP_LOGW(TAG, "settings: stored blob is %u bytes, expected %u; using defaults",
                 static_cast<unsigned>(len), static_cast<unsigned>(sizeof stored));
        return ESP_OK;
    case settings_blob::wrong_magic:
    case settings_blob::wrong_schema:
        ESP_LOGW(TAG, "settings: stored blob is schema %u, this firmware reads %u; using defaults",
                 static_cast<unsigned>(stored.schema), static_cast<unsigned>(kSettingsSchema));
        return ESP_OK;
    case settings_blob::rotation_reset:
        ESP_LOGW(TAG, "settings: stored rotation is not 0 or 180; using the configured %u",
                 static_cast<unsigned>(out->rotation_deg));
        break;
    case settings_blob::accepted:
        break;
    }

    // The addresses are not logged: the network's layout is the installation's, and the
    // log is kept. The SSID is logged as "set", not as a name, for the same reason.
    ESP_LOGI(TAG, "settings: loaded (ssid %s, relay address %s, panel id %s, address %s)",
             out->wifi_ssid[0] ? "set" : "unset",
             out->relay_addr[0] ? "set" : "unset",
             out->panel_id[0] ? "set" : "unset",
             out->ip_mode == kIpModeStatic ? "static" : "DHCP");
    return ESP_OK;
}

esp_err_t settings_save(const relay_settings_t *in)
{
    // A save always writes the current schema, whatever schema it was read from.
    relay_settings_t blob = *in;
    blob.magic = kSettingsMagic;
    blob.schema = kSettingsSchema;

    nvs_handle_t handle = 0;
    ESP_RETURN_ON_ERROR(nvs_open(kNamespace, NVS_READWRITE, &handle), TAG, "open for write");
    const esp_err_t set = nvs_set_blob(handle, kKey, &blob, sizeof blob);
    const esp_err_t commit = set == ESP_OK ? nvs_commit(handle) : set;
    display_resync();
    nvs_close(handle);
    ESP_RETURN_ON_ERROR(commit, TAG, "write");
    ESP_LOGI(TAG, "settings: saved");
    return ESP_OK;
}

namespace {

// One writer of the feed fields at a time: a load, a change and a save are not one NVS operation.
SemaphoreHandle_t feeds_lock()
{
    static SemaphoreHandle_t lock = xSemaphoreCreateMutex();
    return lock;
}

void copy_feeds(relay_settings_t *to, const relay_settings_t *from)
{
    to->feed_stream = from->feed_stream;
    memcpy(to->feed_lang, from->feed_lang, sizeof to->feed_lang);
    to->feed2_stream = from->feed2_stream;
    memcpy(to->feed2_lang, from->feed2_lang, sizeof to->feed2_lang);
    to->feed_split = from->feed_split;
}

}  // namespace

esp_err_t settings_save_feeds(const relay_settings_t *in)
{
    xSemaphoreTake(feeds_lock(), portMAX_DELAY);
    relay_settings_t current;
    esp_err_t err = settings_load(&current);
    if (err == ESP_OK) {
        copy_feeds(&current, in);
        err = settings_save(&current);
    }
    xSemaphoreGive(feeds_lock());
    return err;
}

void settings_adopt_feeds(relay_settings_t *cand)
{
    xSemaphoreTake(feeds_lock(), portMAX_DELAY);
    relay_settings_t current;
    if (settings_load(&current) == ESP_OK) {
        copy_feeds(cand, &current);
    }
    xSemaphoreGive(feeds_lock());
}

esp_err_t settings_ensure_panel_id(relay_settings_t *settings, bool *created)
{
    *created = false;
    if (settings->panel_id[0] != '\0') {
        return ESP_OK;
    }
    uint8_t bytes[16];
    esp_fill_random(bytes, sizeof bytes);
    settings_panel_id_from_bytes(bytes, settings->panel_id);
    ESP_RETURN_ON_ERROR(settings_save(settings), TAG, "save panel id");
    *created = true;
    ESP_LOGI(TAG, "settings: panel id made");
    return ESP_OK;
}

esp_err_t settings_factory_reset()
{
    // The radio first: Wi-Fi writes its own NVS entries as it connects, and one landing after the erase would bring
    // the old network back on the next boot. A radio that was never started answers with an error, which is fine.
    esp_wifi_stop();
    // `nvs_flash_erase` de-initialises the default partition before it erases it, so open handles elsewhere are
    // left dangling. Nothing uses them: the caller restarts.
    ESP_RETURN_ON_ERROR(nvs_flash_erase(), TAG, "factory reset: erase nvs");
    display_resync();
    ESP_LOGW(TAG, "factory reset: settings, panel id and network erased");
    return ESP_OK;
}
