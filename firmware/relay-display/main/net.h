#pragma once

#include <cstdint>

#include "esp_err.h"

// The Wi-Fi radio for the room display. This file starts and stops the
// radio, sets the station's network and address, and reports what the station does.
// It decides nothing about setup mode: `provision.cpp` owns the setup network and
// the page, and tells this file which radio it wants.
//
// Under the emulator (`CONFIG_RELAY_QEMU`) none of the radio is started. The
// decision each call would make is logged, with the line that says the radio was
// skipped, because qemu-system-xtensa has no radio model (see the README).

// How the station gets its address. `dhcp` is the default. A static address is
// applied before the station connects, with the DHCP client stopped, so no lease
// replaces it.
struct net_ip_config {
    bool dhcp;
    uint8_t addr[4];
    uint8_t mask[4];
    uint8_t gateway[4];
    uint8_t dns1[4];
    uint8_t dns2[4];  // 0.0.0.0 when there is none
};

// A network to join. An empty password is an open network.
struct net_sta_config {
    char ssid[33];
    char password[65];
    net_ip_config ip;
};

// The setup network. WPA2 with its passphrase; an empty passphrase is refused by the
// caller, not here.
struct net_ap_config {
    char ssid[33];
    char password[65];
};

// What the station reports. Each callback runs on the event task, so a listener must
// not block; it may call back into this file.
struct net_listener {
    void *ctx;
    void (*on_associated)(void *ctx);                        // WIFI_EVENT_STA_CONNECTED
    void (*on_address)(void *ctx, uint32_t addr_be);         // IP_EVENT_STA_GOT_IP, network order
    void (*on_disconnected)(void *ctx, uint8_t reason);      // WIFI_EVENT_STA_DISCONNECTED
};

// Create the interfaces, the event loop and the Wi-Fi driver, once. Starts nothing.
esp_err_t net_init();

// Where the station's events go. Set before `net_radio_start`.
void net_set_listener(const net_listener *listener);

// Start the radio in the mode the arguments ask for: `ap` alone is the setup network,
// `sta` alone is a station, both is a station beside the setup network. A running
// radio is stopped first. With a station, the station connects as soon as it starts.
esp_err_t net_radio_start(const net_ap_config *ap, const net_sta_config *sta);

// Stop the setup network and keep the station. The station reconnects with the
// configuration it has.
esp_err_t net_radio_drop_ap();

// Leave the network the station is on, without retrying it. A failed attempt uses this,
// so a refused configuration does not keep joining in the background.
esp_err_t net_station_disconnect();

// Join `sta` now, on the running radio, with its address. A connection in progress is
// left first. The result comes back through the listener.
esp_err_t net_station_connect(const net_sta_config *sta);

// Whether a dropped station is retried in the background, with a backoff from 5 s to
// 5 min. Normal operation turns it on. Setup turns it off, so a failed attempt does not
// keep retrying the configuration that was just refused.
void net_set_reconnect(bool on);

// Whether the station holds an address now. The pairing task waits for it before it opens
// a connection to VXP. Cleared on a disconnect, and on every path that stops the
// station, so it is never true for a link that is down.
bool net_station_has_address();

// The wired link the emulator provides. **QEMU only:** `wired_qemu.cpp` defines
// these under `CONFIG_RELAY_QEMU`, and the released board has no Ethernet, so `net.cpp` answers
// for it: nothing started, and no address. The pairing task asks `net_wired_has_address` as well
// as the station's, so the same code runs on the emulator's netif and on the radio.
esp_err_t net_wired_start();
bool net_wired_has_address();
