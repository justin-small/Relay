// The emulator's wired link. **This file is compiled only into the QEMU test image.**
// `main/CMakeLists.txt` adds it, and `esp_eth`, under `CONFIG_RELAY_QEMU`, which only
// `sdkconfig.qemu` sets. The released board has no Ethernet, so the released image neither
// compiles this file nor links the driver, and `tools/firmware_image_check.py` and the link map
// are where that is checked.
//
// **Why a wired link at all.** `qemu-system-xtensa` has no Wi-Fi radio, so the station never gets
// an address under the emulator and the pairing could never run. It does model the Opencores MAC
// (`open_eth`), which the emulated machine always instantiates, and the harness gives that NIC a
// user-mode network (`tests/firmware/qemu.py`): DHCP from the gateway, and the host at 10.0.2.2.
// The pairing and the heartbeat run on whichever netif holds an address, so under the emulator
// the wired one is that netif. The DP83848 PHY is the one ESP-IDF's openeth build is guaranteed
// to have linked, as `components/esp_eth` says; the I/O board's `ethernet_qemu.c` uses the same pair.

#include "esp_check.h"
#include "esp_event.h"
#include "esp_eth_driver.h"
#include "esp_eth_mac_openeth.h"
#include "esp_eth_netif_glue.h"
#include "esp_eth_phy.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "net.h"

namespace {

const char *TAG = "net";

// Set by the events below and read by the pairing task. The events run on the event task, so
// a plain flag is written from one task; the reader only needs to see that it changed.
volatile bool s_wired_address = false;
esp_eth_handle_t s_eth = nullptr;

void on_got_ip(void *, esp_event_base_t, int32_t, void *data)
{
    const auto *e = static_cast<const ip_event_got_ip_t *>(data);
    s_wired_address = true;
    ESP_LOGI(TAG, "net: eth " IPSTR, IP2STR(&e->ip_info.ip));
}

void on_eth_event(void *, esp_event_base_t, int32_t id, void *)
{
    if (id == ETHERNET_EVENT_DISCONNECTED) {
        s_wired_address = false;
        ESP_LOGW(TAG, "net: eth link down");
    }
}

}  // namespace

esp_err_t net_wired_start()
{
    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "netif init");
    const esp_err_t loop = esp_event_loop_create_default();
    if (loop != ESP_OK && loop != ESP_ERR_INVALID_STATE) {
        return loop;
    }

    esp_netif_config_t netif_config = ESP_NETIF_DEFAULT_ETH();
    esp_netif_t *netif = esp_netif_new(&netif_config);
    if (netif == nullptr) {
        return ESP_ERR_NO_MEM;
    }

    eth_mac_config_t mac_config = ETH_MAC_DEFAULT_CONFIG();
    eth_phy_config_t phy_config = ETH_PHY_DEFAULT_CONFIG();
    phy_config.phy_addr = 1;
    phy_config.reset_gpio_num = -1;
    esp_eth_mac_t *mac = esp_eth_mac_new_openeth(&mac_config);
    esp_eth_phy_t *phy = esp_eth_phy_new_dp83848(&phy_config);
    if (mac == nullptr || phy == nullptr) {
        if (phy != nullptr) {
            phy->del(phy);
        }
        if (mac != nullptr) {
            mac->del(mac);
        }
        return ESP_FAIL;
    }

    esp_eth_config_t config = ETH_DEFAULT_CONFIG(mac, phy);
    ESP_RETURN_ON_ERROR(esp_eth_driver_install(&config, &s_eth), TAG, "eth driver");

    // The MAC the board would use, as the I/O board does, so the emulator's link is named the
    // same way the device's own would be.
    uint8_t address[6] = {0};
    if (esp_read_mac(address, ESP_MAC_ETH) == ESP_OK) {
        ESP_RETURN_ON_ERROR(esp_eth_ioctl(s_eth, ETH_CMD_S_MAC_ADDR, address), TAG, "eth mac");
    }
    ESP_RETURN_ON_ERROR(esp_netif_attach(netif, esp_eth_new_netif_glue(s_eth)), TAG, "eth attach");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(IP_EVENT, IP_EVENT_ETH_GOT_IP, on_got_ip, nullptr),
                        TAG, "ip events");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(ETH_EVENT, ESP_EVENT_ANY_ID, on_eth_event, nullptr),
                        TAG, "eth events");
    ESP_RETURN_ON_ERROR(esp_eth_start(s_eth), TAG, "eth start");
    ESP_LOGI(TAG, "net: wired link started under emulation (openeth, DHCP)");
    return ESP_OK;
}

bool net_wired_has_address()
{
    return s_wired_address;
}
