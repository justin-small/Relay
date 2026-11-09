#pragma once

// The station's two start-up decisions, with no ESP-IDF in it. `net.cpp`
// asks these before it touches the radio, and `test_native.cpp` checks them.

// Whether the station should start at all. An empty SSID means nothing has been
// configured yet, and the board stays off the radio until provisioning
// supplies one.
inline bool wifi_should_start(const char *ssid)
{
    return ssid != nullptr && ssid[0] != '\0';
}

// An empty password is an open network. Asking for WPA2 there would make the
// association fail on a network that is perfectly good.
inline bool wifi_is_open(const char *password)
{
    return password == nullptr || password[0] == '\0';
}
