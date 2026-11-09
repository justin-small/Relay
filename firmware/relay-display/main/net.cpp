#include "net.h"

#include <cstring>

#include "display.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "sdkconfig.h"
#include "wifi_policy.h"

namespace {

const char *TAG = "net";

// Reason codes from `esp_wifi_types_generic.h`. Only the one this file reads is named;
// the rest go to the listener as numbers.
constexpr uint8_t kReasonLeft = 8;  // WIFI_REASON_ASSOC_LEAVE: this station asked to leave

// **Backoff, not a fixed wait.** A configured panel whose network is down
// retries, with the wait doubling from 5 s to a 5 min ceiling. A fixed short wait
// against a network that is genuinely down is a radio that is busy all day, and a
// backoff that never ends would be a panel that takes minutes to notice the network
// came back. Five minutes is the ceiling because that is what a person waits before
// they go and look at the panel.
constexpr uint64_t kRetryMinUs = 5ULL * 1000 * 1000;
constexpr uint64_t kRetryMaxUs = 300ULL * 1000 * 1000;

#if !CONFIG_RELAY_QEMU
esp_netif_t *s_sta = nullptr;
esp_netif_t *s_ap = nullptr;
esp_timer_handle_t s_retry_timer = nullptr;
bool s_inited = false;
bool s_started = false;
bool s_station_wanted = false;  // a station is configured; a radio start connects it
bool s_reconnect = false;
bool s_link_up = false;
bool s_has_address = false;
uint64_t s_backoff_us = kRetryMinUs;
net_sta_config s_sta_cfg = {};
const net_listener *s_listener = nullptr;

void retry_connect(void *)
{
    const esp_err_t err = esp_wifi_connect();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "wifi: reconnect refused (%s)", esp_err_to_name(err));
    }
}

void arm_retry()
{
    // Stop first, so a second disconnect while the timer is pending re-arms it instead
    // of failing the start. Either way it is one retry.
    esp_timer_stop(s_retry_timer);
    ESP_LOGW(TAG, "wifi: retrying in %llu s", static_cast<unsigned long long>(s_backoff_us / 1000000));
    esp_timer_start_once(s_retry_timer, s_backoff_us);
    s_backoff_us = s_backoff_us * 2 > kRetryMaxUs ? kRetryMaxUs : s_backoff_us * 2;
}

void on_wifi_event(void *, esp_event_base_t, int32_t id, void *data)
{
    switch (id) {
    case WIFI_EVENT_STA_START:
        if (s_station_wanted) {
            esp_timer_stop(s_retry_timer);
            esp_wifi_connect();
        }
        break;
    case WIFI_EVENT_STA_CONNECTED:
        s_link_up = true;
        if (s_listener != nullptr && s_listener->on_associated != nullptr) {
            s_listener->on_associated(s_listener->ctx);
        }
        break;
    case WIFI_EVENT_STA_DISCONNECTED: {
        const auto *e = static_cast<const wifi_event_sta_disconnected_t *>(data);
        s_link_up = false;
        s_has_address = false;
        // The reason is a number, not text: it names the network's answer, and nothing
        // in it is a password.
        ESP_LOGW(TAG, "wifi: disconnected (reason %d)", e->reason);
        if (s_listener != nullptr && s_listener->on_disconnected != nullptr) {
            s_listener->on_disconnected(s_listener->ctx, e->reason);
        }
        // A disconnect this station asked for is not a fault to retry from.
        if (s_reconnect && s_station_wanted && e->reason != kReasonLeft) {
            arm_retry();
        }
        break;
    }
    default:
        break;
    }
}

void on_ip_event(void *, esp_event_base_t, int32_t id, void *data)
{
    if (id != IP_EVENT_STA_GOT_IP) {
        return;
    }
    const auto *e = static_cast<const ip_event_got_ip_t *>(data);
    ESP_LOGI(TAG, "net: got address " IPSTR, IP2STR(&e->ip_info.ip));
    s_backoff_us = kRetryMinUs;
    s_has_address = true;
    if (s_listener != nullptr && s_listener->on_address != nullptr) {
        s_listener->on_address(s_listener->ctx, e->ip_info.ip.addr);
    }
}

// Copy `src` into a fixed field of `cap` bytes, terminated when there is room. A 32-byte
// network name fills its field and has no terminator, which the station accepts. The
// ESP-IDF structures hold their SSID and password as byte arrays, and the length has
// already been bounded by the form, so this only keeps a bad value from writing past
// the field.
void copy_field(uint8_t *dst, size_t cap, const char *src)
{
    size_t n = 0;
    while (n < cap && src[n] != '\0') {
        ++n;
    }
    memcpy(dst, src, n);
    if (n < cap) {
        dst[n] = '\0';
    }
}

// Put `bytes` into an ESP-IDF address. The bytes are in network order, so a copy is
// the address; no byte swap is needed on the chip, which is little-endian.
void set_address(esp_ip4_addr_t *dst, const uint8_t bytes[4])
{
    memcpy(&dst->addr, bytes, 4);
}

// The station's address: DHCP, or a fixed address with the DHCP client stopped first.
// Called before the station connects, so a lease cannot replace the static address
// after it is applied (the I/O board's `set_static_ip` says the same).
esp_err_t apply_ip(const net_ip_config &cfg)
{
    if (cfg.dhcp) {
        const esp_err_t err = esp_netif_dhcpc_start(s_sta);
        if (err != ESP_OK && err != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STARTED) {
            ESP_LOGW(TAG, "net: DHCP client did not start (%s)", esp_err_to_name(err));
            return err;
        }
        return ESP_OK;
    }

    const esp_err_t stopped = esp_netif_dhcpc_stop(s_sta);
    if (stopped != ESP_OK && stopped != ESP_ERR_ESP_NETIF_DHCP_ALREADY_STOPPED) {
        ESP_LOGW(TAG, "net: DHCP client did not stop (%s)", esp_err_to_name(stopped));
        return stopped;
    }
    esp_netif_ip_info_t info = {};
    set_address(&info.ip, cfg.addr);
    set_address(&info.netmask, cfg.mask);
    set_address(&info.gw, cfg.gateway);
    ESP_RETURN_ON_ERROR(esp_netif_set_ip_info(s_sta, &info), TAG, "static address");

    // The secondary is cleared when it is not given, so a previous value does not
    // stay in the station.
    esp_netif_dns_info_t dns = {};
    dns.ip.type = ESP_IPADDR_TYPE_V4;
    set_address(&dns.ip.u_addr.ip4, cfg.dns1);
    ESP_RETURN_ON_ERROR(esp_netif_set_dns_info(s_sta, ESP_NETIF_DNS_MAIN, &dns), TAG, "primary DNS");
    set_address(&dns.ip.u_addr.ip4, cfg.dns2);
    ESP_RETURN_ON_ERROR(esp_netif_set_dns_info(s_sta, ESP_NETIF_DNS_BACKUP, &dns), TAG, "secondary DNS");
    ESP_LOGI(TAG, "net: static address applied, DHCP client stopped");
    return ESP_OK;
}

esp_err_t set_station(const net_sta_config &sta)
{
    wifi_config_t cfg = {};
    copy_field(cfg.sta.ssid, sizeof cfg.sta.ssid, sta.ssid);
    copy_field(cfg.sta.password, sizeof cfg.sta.password, sta.password);
    // An empty password is an open network (`wifi_is_open`).
    cfg.sta.threshold.authmode = wifi_is_open(sta.password) ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
    cfg.sta.pmf_cfg.capable = true;
    cfg.sta.pmf_cfg.required = false;
    // **WPA3 by either derivation of its password element.** ESP-IDF's default is hunting-and-pecking alone, and
    // a WPA3 network that offers only hash-to-element (current routers, and every WPA3 network on 6 GHz) is then
    // "found with no compatible security", reason 210. The bench's home network refused the panel exactly so
    // (2026-10-28). Both lets the panel join either kind, and a WPA2 network is unaffected.
    cfg.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    return esp_wifi_set_config(WIFI_IF_STA, &cfg);
}

esp_err_t set_access_point(const net_ap_config &ap)
{
    wifi_config_t cfg = {};
    const size_t ssid_len = strnlen(ap.ssid, sizeof cfg.ap.ssid);
    memcpy(cfg.ap.ssid, ap.ssid, ssid_len);
    cfg.ap.ssid_len = static_cast<uint8_t>(ssid_len);
    copy_field(cfg.ap.password, sizeof cfg.ap.password, ap.password);
    // WPA2, never open: the setup page carries the Wi-Fi password, and the setup network
    // is the only thing that stops a stranger in the lobby from typing it.
    cfg.ap.authmode = WIFI_AUTH_WPA2_PSK;
    cfg.ap.max_connection = 4;
    cfg.ap.channel = 1;
    cfg.ap.pmf_cfg.capable = true;
    cfg.ap.pmf_cfg.required = false;
    return esp_wifi_set_config(WIFI_IF_AP, &cfg);
}

#else  // CONFIG_RELAY_QEMU

// **Under emulation the radio is not started, and the decision is logged instead**
//. qemu-system-xtensa has no Wi-Fi model: `esp_wifi_start` never returns on it.
// The decision the board would make is logged, with the line that says the radio was
// skipped, so the emulator's log shows what the board would have done.
esp_err_t emulated_skip()
{
    ESP_LOGW(TAG, "net: Wi-Fi skipped under emulation (QEMU has no radio)");
    return ESP_OK;
}

#endif

}  // namespace

esp_err_t net_init()
{
#if CONFIG_RELAY_QEMU
    return ESP_OK;
#else
    if (s_inited) {
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "netif init");
    const esp_err_t loop = esp_event_loop_create_default();
    if (loop != ESP_OK && loop != ESP_ERR_INVALID_STATE) {
        return loop;
    }
    s_sta = esp_netif_create_default_wifi_sta();
    s_ap = esp_netif_create_default_wifi_ap();
    if (s_sta == nullptr || s_ap == nullptr) {
        ESP_LOGE(TAG, "net: could not create the Wi-Fi interfaces");
        return ESP_FAIL;
    }

    const wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&init_cfg), TAG, "wifi init");
    ESP_RETURN_ON_ERROR(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event,
                                                            nullptr, nullptr),
                        TAG, "wifi events");
    ESP_RETURN_ON_ERROR(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_ip_event,
                                                            nullptr, nullptr),
                        TAG, "ip events");

    const esp_timer_create_args_t timer_args = {
        .callback = retry_connect,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "wifi_retry",
        .skip_unhandled_events = false,
    };
    ESP_RETURN_ON_ERROR(esp_timer_create(&timer_args, &s_retry_timer), TAG, "retry timer");
    s_inited = true;
    return ESP_OK;
#endif
}

void net_set_listener(const net_listener *listener)
{
#if !CONFIG_RELAY_QEMU
    s_listener = listener;
#else
    (void)listener;
#endif
}

void net_set_reconnect(bool on)
{
#if !CONFIG_RELAY_QEMU
    s_reconnect = on;
    if (on) {
        s_backoff_us = kRetryMinUs;
    } else {
        esp_timer_stop(s_retry_timer);
    }
#else
    (void)on;
#endif
}

esp_err_t net_radio_start(const net_ap_config *ap, const net_sta_config *sta)
{
#if CONFIG_RELAY_QEMU
    if (sta != nullptr) {
        ESP_LOGW(TAG, "net: would start Wi-Fi for SSID '%s' if the radio were present", sta->ssid);
    }
    if (ap != nullptr) {
        ESP_LOGW(TAG, "net: would open the setup network if the radio were present");
    }
    return emulated_skip();
#else
    ESP_RETURN_ON_ERROR(net_init(), TAG, "net init");
    if (s_started) {
        // Stopped first: the mode cannot change on a running radio. The error when it was
        // not running is not interesting.
        esp_wifi_stop();
        s_started = false;
    }
    const wifi_mode_t mode = ap != nullptr && sta != nullptr ? WIFI_MODE_APSTA
                             : ap != nullptr                 ? WIFI_MODE_AP
                                                             : WIFI_MODE_STA;
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(mode), TAG, "wifi mode");
    if (ap != nullptr) {
        ESP_RETURN_ON_ERROR(set_access_point(*ap), TAG, "setup network config");
    }
    s_station_wanted = sta != nullptr;
    if (sta != nullptr) {
        s_sta_cfg = *sta;
        ESP_RETURN_ON_ERROR(apply_ip(sta->ip), TAG, "address");
        ESP_RETURN_ON_ERROR(set_station(*sta), TAG, "station config");
        ESP_LOGI(TAG, "net: station for SSID '%s'", sta->ssid);
    }
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "wifi start");
    display_resync();  // Wi-Fi's start writes its calibration and configuration to NVS
    s_started = true;
    return ESP_OK;
#endif
}

esp_err_t net_radio_drop_ap()
{
#if CONFIG_RELAY_QEMU
    ESP_LOGW(TAG, "net: would close the setup network if the radio were present");
    return emulated_skip();
#else
    if (!s_started) {
        return ESP_OK;
    }
    // A started radio cannot change its mode, so it is stopped, moved to station-only and
    // started again. The start connects the station, with the configuration it has.
    esp_wifi_stop();
    s_started = false;
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "station mode");
    s_station_wanted = true;
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "wifi start");
    display_resync();  // Wi-Fi's start writes its calibration and configuration to NVS
    s_started = true;
    ESP_LOGI(TAG, "net: setup network closed; the station stays up");
    return ESP_OK;
#endif
}

esp_err_t net_station_disconnect()
{
#if CONFIG_RELAY_QEMU
    return emulated_skip();
#else
    s_station_wanted = false;
    esp_timer_stop(s_retry_timer);
    return esp_wifi_disconnect();
#endif
}

esp_err_t net_station_connect(const net_sta_config *sta)
{
#if CONFIG_RELAY_QEMU
    (void)sta;
    return emulated_skip();
#else
    if (sta == nullptr || !s_started) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_timer_stop(s_retry_timer);
    // **A first setup runs the radio as an access point alone**, because there is no stored network to join, and
    // a station's configuration is refused on a radio with no station (ESP_ERR_WIFI_MODE). Every first attempt
    // failed with "Could not start the connection" until this was read on the bench (2026-10-28). The station is
    // added beside the access point on the running radio, which keeps the setup network and the phone on it.
    wifi_mode_t mode = WIFI_MODE_NULL;
    ESP_RETURN_ON_ERROR(esp_wifi_get_mode(&mode), TAG, "wifi mode");
    if (mode == WIFI_MODE_AP) {
        ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_APSTA), TAG, "station beside the setup network");
        ESP_LOGI(TAG, "net: station added beside the setup network");
    }
    s_sta_cfg = *sta;
    s_station_wanted = true;
    ESP_RETURN_ON_ERROR(apply_ip(sta->ip), TAG, "address");
    ESP_RETURN_ON_ERROR(set_station(*sta), TAG, "station config");
    // A link that is up is left first; its disconnect arrives as "left", which the
    // listener is told about and does not treat as a failure of the new attempt.
    if (s_link_up) {
        esp_wifi_disconnect();
    }
    return esp_wifi_connect();
#endif
}

bool net_station_has_address()
{
#if !CONFIG_RELAY_QEMU
    return s_has_address;
#else
    return false;
#endif
}

#if !CONFIG_RELAY_QEMU
// The released board has no wired link. `wired_qemu.cpp` is the only definition under emulation.
esp_err_t net_wired_start()
{
    return ESP_OK;
}

bool net_wired_has_address()
{
    return false;
}
#endif
