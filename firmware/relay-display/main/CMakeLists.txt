# The Relay panel's own code. The components it calls:
#
#   esp_lcd            the RGB panel driver (display.cpp)
#   esp_driver_gpio    the RGB panel's pins, and the BOOT button (button.cpp)
#   esp_driver_i2c     the board's I2C bus: the STC8 that switches the backlight (board_i2c.cpp), the GT911 touch (touch.cpp)
#   esp_psram          the PSRAM size on the boot line; the frame buffer's home
#   esp_wifi, esp_netif, esp_event   the radio and the station (net.cpp)
#   esp_http_server    the setup page, served only while the setup network is open (provision.cpp)
#   esp_hw_support     esp_fill_random for the passphrase and the panel ID, esp_read_mac for the name
#   esp_timer          the retry backoff and the setup network's timers, the caption stream's silence clock
#   (panel_link.cpp checks in with Relay's panel registry and keeps its event stream open, over the same lwip sockets)
#   app_update, esp_partition, mbedtls   the firmware update (firmware_update.cpp): the spare slot, its signature check, the image's SHA-256
#   mdns               browsing for Relay's `_relay-captions._tcp` announcement (discovery.cpp); the managed component
#                      `espressif/mdns` (idf_component.yml)
#   lwip               getpeername, so the setup page can refuse a client off its network; the caption
#                      stream's socket and name lookup (captions.cpp)
#   nvs_flash          NVS, and the settings blob (settings.cpp)
#   relay_auth, relay_form   the Origin check and the form parser the setup page uses
#   relay_button       the BOOT button's hold rules
#   relay_captive      the setup network's address and its DNS answer
#   lvgl               the drawing library (screen.cpp, caption_view.cpp), with the settings in lvgl_config/
#
# The `relay_*` components are in `components/` beside this target and are header-only, so
# listing them adds include paths and nothing to the image's code. They are copies of the VXP
# firmware's shared components, and belong to this target alone (README.md, "Copied from VXP").
set(_relay_requires
    esp_lcd
    esp_driver_gpio
    esp_driver_i2c
    esp_psram
    esp_wifi
    esp_netif
    esp_event
    esp_http_server
    esp_hw_support
    esp_timer
    lwip
    mdns
    nvs_flash
    app_update
    esp_partition
    mbedtls
    esp_eth
    relay_auth
    relay_button
    relay_captive
    relay_form
    lvgl)

# The emulator's virtual RGB panel. Its managed component is fetched only when `CONFIG_RELAY_QEMU`
# is on, which `idf_component.yml` checks, so the shipped image does not require it.
if(CONFIG_RELAY_QEMU)
    list(APPEND _relay_requires esp_lcd_qemu_rgb)
endif()

# The emulator's wired link: the Opencores MAC and its driver, which only the emulator's image has.
# The released board has no Ethernet, so neither the driver nor this source is in its build.
# `sdkconfig.qemu` turns the MAC on. `esp_eth` is required unconditionally: in a build without
# CONFIG_ETH_ENABLED the component is config-only and builds no library, so the released image links
# nothing from it. Only the source that calls it is QEMU-only.
set(_relay_qemu_srcs "")
if(CONFIG_RELAY_QEMU)
    set(_relay_qemu_srcs "wired_qemu.cpp")
endif()

# `test_native.cpp` is deliberately not in SRCS. It is a `main()` compiled by g++ on a laptop, and
# `build.sh` runs it before anything is built for the board. Linking it here would be a second `main`
# in the application. The headers it checks (`panel_format.h`, `settings_format.h`, `wifi_policy.h`,
# `setup_format.h`, `setup_page.h`, `caption_format.h`, `feed_format.h`, `service_format.h`, `discovery_format.h`, `panel_link_format.h`) hold no ESP-IDF calls, so the same files serve both builds.
idf_component_register(
    SRCS "main.cpp" "display.cpp" "board_i2c.cpp" "button.cpp" "net.cpp" "settings.cpp" "diag.cpp"
         "provision.cpp" "screen.cpp" "touch.cpp" "captions.cpp" "caption_view.cpp" "service_view.cpp" "service_control.cpp" "feeds.cpp" "discovery.cpp" "panel_link.cpp" "firmware_update.cpp"
         "fonts/relay_caption_36.c" "fonts/relay_caption_36_more.c"
         ${_relay_qemu_srcs}
    INCLUDE_DIRS "."
    REQUIRES ${_relay_requires}
)
