#include "discovery.h"

#include <cstring>

#include "discovery_format.h"
#include "esp_log.h"
#include "feeds.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mdns.h"
#include "net.h"
#include "settings.h"

namespace {

const char *TAG = "discovery";

constexpr uint32_t kBrowseMs = 3000;      // how long one browse listens
constexpr uint32_t kRetryMs = 5000;       // between browses while nothing is found
constexpr uint32_t kRefreshMs = 30000;    // between browses once Relay is found: follow it if it moves

// The first IPv4 address of a result, if it has one.
bool first_ipv4(const mdns_result_t *r, uint8_t out[4])
{
    for (const mdns_ip_addr_t *a = r->addr; a != nullptr; a = a->next) {
        if (a->addr.type == ESP_IPADDR_TYPE_V4) {
            memcpy(out, &a->addr.u_addr.ip4.addr, 4);  // already in network order
            return true;
        }
    }
    return false;
}

// One browse. Returns how many usable answers are in `list`.
int browse(relay_found *list)
{
    mdns_result_t *results = nullptr;
    const esp_err_t err = mdns_query_ptr(kRelayService, kRelayProto, kBrowseMs, kRelayFoundMax, &results);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "discovery: browse failed (%s)", esp_err_to_name(err));
        return 0;
    }
    int n = 0;
    for (const mdns_result_t *r = results; r != nullptr && n < kRelayFoundMax; r = r->next) {
        relay_found f = {};
        f.port = r->port;
        if (!first_ipv4(r, f.ip) && r->hostname != nullptr) {
            // The announcement came without its address record: ask for the host's.
            esp_ip4_addr_t ip = {};
            if (mdns_query_a(r->hostname, 2000, &ip) == ESP_OK) {
                memcpy(f.ip, &ip.addr, 4);
            }
        }
        const char *name = r->instance_name;
        for (size_t i = 0; i < r->txt_count; ++i) {
            if (strcmp(r->txt[i].key, "name") == 0 && r->txt[i].value != nullptr) {
                name = r->txt[i].value;
            }
        }
        relay_name_clean(name, f.name, sizeof f.name);
        if (relay_found_usable(f)) {
            list[n++] = f;
        }
    }
    mdns_query_results_free(results);
    return n;
}

void task(void *)
{
    // The browse needs an address, and the radio may take a while to give one.
    while (!net_station_has_address() && !net_wired_has_address()) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    if (mdns_init() != ESP_OK) {
        ESP_LOGE(TAG, "discovery: mDNS did not start; set the Relay address on the setup page");
        vTaskDelete(nullptr);
        return;
    }

    char saved[kRelayNameCap] = {};
    settings_load_relay_name(saved, sizeof saved);
    char current[sizeof(relay_settings_t::relay_addr)] = {};
    relay_found list[kRelayFoundMax];
    bool warned_missing = false;

    for (;;) {
        const int n = browse(list);
        const int pick = relay_pick(list, n, saved);
        if (pick >= 0) {
            warned_missing = false;
            char url[sizeof current];
            if (relay_url_make(list[pick], url, sizeof url) && strcmp(url, current) != 0) {
                ESP_LOGI(TAG, "discovery: Relay \"%s\" at %s", list[pick].name, url);
                feeds_use_relay(url);
                memcpy(current, url, sizeof current);
                if (saved[0] == '\0' && list[pick].name[0] != '\0') {
                    // The first Relay this panel meets is the one it stays with.
                    memcpy(saved, list[pick].name, sizeof saved);
                    if (settings_save_relay_name(saved) != ESP_OK) {
                        ESP_LOGW(TAG, "discovery: the Relay's name could not be saved");
                    }
                }
            }
        } else if (!warned_missing) {
            warned_missing = true;
            if (saved[0] != '\0') {
                ESP_LOGI(TAG, "discovery: Relay \"%s\" not answering (%d other found)", saved, n);
            } else {
                ESP_LOGI(TAG, "discovery: no Relay answering yet");
            }
        }
        vTaskDelay(pdMS_TO_TICKS(pick >= 0 ? kRefreshMs : kRetryMs));
    }
}

}  // namespace

esp_err_t discovery_start(const relay_settings_t &settings)
{
    if (settings.relay_addr[0] != '\0') {
        ESP_LOGI(TAG, "discovery: off, the Relay address is set");
        return ESP_OK;
    }
    return xTaskCreatePinnedToCore(task, "discovery", 5120, nullptr, 2, nullptr, 0) == pdPASS ? ESP_OK
                                                                                               : ESP_ERR_NO_MEM;
}
