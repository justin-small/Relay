#include "provision.h"

#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "auth.h"
#include "config.h"
#include "diag.h"
#include "display.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "net.h"
#include "panel_format.h"
#include "screen.h"
#include "sdkconfig.h"
#include "settings.h"
#include "setup_format.h"
#include "setup_page.h"
#include "touch.h"
#include "caption_view.h"
#include "captions.h"
#include "feeds.h"
#include "panel_link.h"
#include "wifi_policy.h"

#if !CONFIG_RELAY_QEMU
#include "esp_http_server.h"
#include "lwip/sockets.h"
#endif

// What this file is for, and what it is not. It opens the setup network and serves the
// page, decides whether a submitted configuration can be used, tries it, saves it, and
// closes the network again. It does not talk to Relay: the caption stream comes after it,
// and reads the settings this file saves.

namespace {

const char *TAG = "provision";

// How long an attempt may run before it is called a failure. The DHCP client's own
// wait is shorter than this on a healthy network; the figure covers a slow access point
// and a lease that takes two tries.
constexpr int64_t kAttemptTimeoutUs = 30LL * 1000 * 1000;

// After a success the setup network stays up this long, so the page can show the result
// and the installer can read the address. A minute is enough to read it and short
// enough that the network is not left open.
constexpr int64_t kCloseGraceUs = 60LL * 1000 * 1000;

// A recovery that is left alone closes its setup network after this long with no use
// (the setup access point is not left enabled for no reason).
constexpr int64_t kRecoveryIdleUs = 15LL * 60 * 1000 * 1000;
constexpr int64_t kIdleCheckUs = 60LL * 1000 * 1000;

#if !CONFIG_RELAY_QEMU
// The largest form a valid submission can be: the longest name, password and address
// each percent-encoded, with the field names. Anything larger is refused before it is
// read into memory.
constexpr size_t kBodyMax = 1536;
constexpr size_t kPageMax = 6144;
#endif

// The panel's short text for each failure. The page has the full sentence; the panel is
// 800 pixels wide, and a line at the size the panel shows a name in has room for about
// thirty characters.
const char *panel_failure_text(setup_failure f)
{
    switch (f) {
    case setup_failure::none:
        return "";
    case setup_failure::wrong_password:
        return "Password refused";
    case setup_failure::network_not_found:
        return "Network not found";
    case setup_failure::network_refused:
        return "Network refused the connection";
    case setup_failure::link_lost:
        return "Connection lost";
    case setup_failure::no_address:
        return "No address from the network";
    case setup_failure::no_response:
        return "The network did not respond";
    case setup_failure::could_not_save:
        return "Settings could not be saved";
    case setup_failure::could_not_start:
        return "Could not start the connection";
    }
    return "";
}

// The state of the setup network and of the station, guarded by `s_lock`. Nothing in
// here is a secret except the passphrase and the stored settings, and neither is logged
// or sent to a page: the page renders from `page_view_t`, which has no password field.
struct state_t {
    bool open = false;        // the setup network is up
    bool recovery = false;    // opened by recovery, with a stored network to return to
    bool connecting = false;  // an attempt is running
    bool associated = false;  // the attempt has associated with the access point
    bool connected = false;   // the attempt succeeded and the settings are saved
    bool failed = false;      // the last attempt failed; `failure` says why
    setup_failure failure = setup_failure::none;
    const char *notice = nullptr;  // a refused form's message, a static string
    char network[33] = {0};        // the name being joined, or joined
    char address[16] = {0};        // the address the attempt got, for the connected page
    char passphrase[kSetupPassphraseLength + 1] = {0};
    char ap_ssid[17] = {0};
    relay_settings_t stored = {};     // what NVS holds, as last read or saved
    relay_settings_t candidate = {};  // the attempt's settings, saved only on success
    setup_form_t form = {};            // the last submitted form, to show again
    bool has_form = false;
    int64_t last_activity_us = 0;
    bool link_down = false;           // normal mode: the station lost its network
    setup_failure last_drop = setup_failure::none;
};

state_t s_;
bool s_ready = false;
SemaphoreHandle_t s_lock = nullptr;
// Every paint goes through this one mutex. The panel driver and its strip buffer are
// shared, and the event task, the HTTP task and the timers all paint.
SemaphoreHandle_t s_paint_lock = nullptr;
// One open at a time. The serial console and the BOOT button both call `provision_open_setup`, each
// from its own task, and an open is not atomic: two could each see the network closed and each start the radio
// and the page. The alternative was a queue to the console's task, a second task whose only job is to take turns,
// with its own stack and its own failure path. A lock serialises the one function that races and leaves each
// caller to do its own work.
SemaphoreHandle_t s_open_lock = nullptr;

esp_timer_handle_t s_attempt_timer = nullptr;
esp_timer_handle_t s_close_timer = nullptr;
esp_timer_handle_t s_idle_timer = nullptr;

#if !CONFIG_RELAY_QEMU
httpd_handle_t s_http = nullptr;
// One page and one body at a time. Every handler runs on the HTTP server's task, so
// these are never in use twice.
char s_page[kPageMax];
char s_body[kBodyMax + 1];
#endif

void lock() { xSemaphoreTake(s_lock, portMAX_DELAY); }
void unlock() { xSemaphoreGive(s_lock); }

// Holds `s_open_lock` for one open. A guard and not a pair of calls: `provision_open_setup` returns early from
// several places, and each one must give the lock back.
class OpenLock {
public:
    OpenLock() { xSemaphoreTake(s_open_lock, portMAX_DELAY); }
    ~OpenLock() { xSemaphoreGive(s_open_lock); }
    OpenLock(const OpenLock &) = delete;
    OpenLock &operator=(const OpenLock &) = delete;
};

int64_t now_us() { return esp_timer_get_time(); }

// Copy the name of a stored or typed network into a fixed field, bounded by its size.
void copy_name(char *dst, size_t cap, const char *src)
{
    settings_set_text(dst, cap, src);
}

// A station configuration from stored settings: the name, the password and the address
// mode with its values.
net_sta_config sta_from_settings(const relay_settings_t &s)
{
    net_sta_config sta = {};
    copy_name(sta.ssid, sizeof sta.ssid, s.wifi_ssid);
    copy_name(sta.password, sizeof sta.password, s.wifi_password);
    sta.ip.dhcp = s.ip_mode != kIpModeStatic;
    memcpy(sta.ip.addr, s.static_addr, 4);
    memcpy(sta.ip.mask, s.static_mask, 4);
    memcpy(sta.ip.gateway, s.static_gateway, 4);
    memcpy(sta.ip.dns1, s.static_dns1, 4);
    memcpy(sta.ip.dns2, s.static_dns2, 4);
    return sta;
}

// Draw a new passphrase from the hardware random number generator. Each byte is one
// character's chance, and a byte the alphabet rejects is simply not used.
void generate_passphrase(char out[kSetupPassphraseLength + 1])
{
    int n = 0;
    while (n < kSetupPassphraseLength) {
        uint8_t buf[16];
        esp_fill_random(buf, sizeof buf);
        for (size_t i = 0; i < sizeof buf && n < kSetupPassphraseLength; ++i) {
            if (setup_passphrase_char(buf[i], &out[n])) {
                ++n;
            }
        }
    }
    out[kSetupPassphraseLength] = '\0';
}

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------

// The lines of one screen, with their text held here while the screen is drawn.
struct panel_lines_t {
    char text[kPanelTextMaxLines][64];
    panel_text_line lines[kPanelTextMaxLines];
    int count = 0;

    void add(const char *s, int scale, bool mono = false)
    {
        if (count >= kPanelTextMaxLines) {
            return;
        }
        copy_name(text[count], sizeof text[count], s);
        lines[count].text = text[count];
        lines[count].scale = scale;
        lines[count].mono = mono;
        ++count;
    }
};

// Draw the panel for the current state. Called from every task that changes the state,
// so the screen always shows the last thing that happened.
void draw_panel()
{
    xSemaphoreTake(s_paint_lock, portMAX_DELAY);

    // Copy under the state lock, then paint with nothing held that another task needs.
    lock();
    const bool open = s_.open;
    const bool connecting = s_.connecting;
    const bool connected = s_.connected;
    const bool failed = s_.failed;
    const setup_failure failure = s_.failure;
    const bool link_down = s_.link_down;
    const setup_failure last_drop = s_.last_drop;
    char network[33];
    char address[16];
    char passphrase[kSetupPassphraseLength + 1];
    char ap_ssid[17];
    char stored_ssid[33];
    memcpy(network, s_.network, sizeof network);
    memcpy(address, s_.address, sizeof address);
    memcpy(passphrase, s_.passphrase, sizeof passphrase);
    memcpy(ap_ssid, s_.ap_ssid, sizeof ap_ssid);
    memcpy(stored_ssid, s_.stored.wifi_ssid, sizeof stored_ssid);
    unlock();

    panel_lines_t p;
    // The setup network's join code, drawn beside its lines so a phone's camera joins without typing (empty for
    // every other screen). It holds the name and passphrase the lines already print, and nothing more.
    char qr[160] = "";
    if (open && connecting) {
        p.add("Connecting", 5);
        p.add(network, 4);
        p.add("This takes about 30 seconds", 3);
    } else if (open && connected) {
        char joined[64];
        snprintf(joined, sizeof joined, "Connected to %s", network);
        p.add("Step 2 of 2", 3);
        p.add(joined, 4);
        p.add("Address", 3);
        p.add(address, 5);
        p.add("Setup is done. This screen closes in a minute.", 3);
    } else if (open) {
        p.add("Step 1 of 2", 3);
        p.add("Join this Wi-Fi", 5);
        p.add("Network", 3);
        p.add(ap_ssid, 4);
        p.add("Password", 3);
        p.add(passphrase, 5);
        p.add("Step 2: on that phone, open", 3);
        p.add(kSetupPageAddress, 4);
        p.add(failed ? panel_failure_text(failure) : "Waiting for a phone or computer", 3);
        setup_wifi_qr(ap_ssid, passphrase, qr, sizeof qr);
    } else if (link_down) {
        p.add("Relay panel", 4);
        p.add("Network", 3);
        p.add(stored_ssid, 4);
        p.add(panel_failure_text(last_drop), 3);
        p.add("Retrying in the background", 3);
        p.add("To change the network:", 3);
        // The BOOT hint is the longest line the panel prints (56 characters), so the buffer is sized for it.
        // A longer line is cut at the buffer, and the native check holds the hint to the width of its line.
        p.add("Hold BOOT for 5 s, or type recover on the serial console", 3);
    }

    if (p.count > 0) {
        if (screen_show_lines(p.lines, p.count, qr) != ESP_OK) {
            ESP_LOGE(TAG, "panel: the screen did not reach the panel");
        }
    } else {
        // Nothing to say: the captions when a feed is chosen, and otherwise the bench picture.
        if (captions_state(0) != caption_state::off) {
            screen_show_captions();
        } else if (screen_show_bench(RELAY_FW_VERSION) != ESP_OK) {
            ESP_LOGE(TAG, "panel: the bench picture did not reach the panel");
        }
    }
    xSemaphoreGive(s_paint_lock);
}

// ---------------------------------------------------------------------------
// The attempt
// ---------------------------------------------------------------------------

void attempt_failed(setup_failure f)
{
    lock();
    if (!s_.connecting) {
        unlock();
        return;
    }
    s_.connecting = false;
    s_.connected = false;
    s_.failed = true;
    s_.failure = f;
    s_.notice = setup_failure_text(f);
    unlock();
    esp_timer_stop(s_attempt_timer);
    // The station is left disconnected, so the refused configuration does not keep joining
    // in the background. The stored settings are untouched.
    net_station_disconnect();
    ESP_LOGW(TAG, "setup: attempt failed: %s", setup_failure_text(f));
    draw_panel();
}

void attempt_succeeded(const uint8_t addr[4])
{
    relay_settings_t cand;
    lock();
    if (!s_.connecting) {
        unlock();
        return;
    }
    cand = s_.candidate;
    unlock();

    // The settings are written only now: a network that did not answer leaves NVS as it
    // was. NVS writes the blob whole or not at all, so a power cut here keeps the old one.
    esp_timer_stop(s_attempt_timer);
    // The candidate was copied before the setup page opened, and a person may have chosen a feed since: the stored
    // choice is kept, not put back.
    settings_adopt_feeds(&cand);
    const esp_err_t saved = settings_save(&cand);

    lock();
    s_.connecting = false;
    if (saved != ESP_OK) {
        // The network answered and the write did not. The attempt is over, the stored
        // settings are as they were, and the station is left off the network.
        s_.connected = false;
        s_.failed = true;
        s_.failure = setup_failure::could_not_save;
        s_.notice = setup_failure_text(setup_failure::could_not_save);
        unlock();
        net_station_disconnect();
        ESP_LOGW(TAG, "setup: attempt failed: %s", setup_failure_text(setup_failure::could_not_save));
        draw_panel();
        return;
    }
    s_.stored = cand;
    s_.connected = true;
    s_.failed = false;
    s_.failure = setup_failure::none;
    s_.notice = nullptr;
    ipv4_text(addr, s_.address);
    unlock();
    ESP_LOGI(TAG, "setup: settings saved; the setup network closes in 60 s");
    esp_timer_stop(s_close_timer);
    esp_timer_start_once(s_close_timer, kCloseGraceUs);
    draw_panel();
}

void attempt_timeout(void *)
{
    lock();
    const bool associated = s_.associated;
    unlock();
    attempt_failed(associated ? setup_failure::no_address : setup_failure::no_response);
}

// ---------------------------------------------------------------------------
// The station's events
// ---------------------------------------------------------------------------

void on_associated(void *)
{
    lock();
    if (!s_.connecting) {
        unlock();
        return;
    }
    s_.associated = true;
    const bool static_address = s_.candidate.ip_mode == kIpModeStatic;
    uint8_t addr[4];
    memcpy(addr, s_.candidate.static_addr, 4);
    unlock();
    // A static address needs no lease, so the association is the end of the attempt. The
    // address was applied before the connect, and the page says which address it is.
    if (static_address) {
        attempt_succeeded(addr);
    }
}

void on_address(void *, uint32_t addr_be)
{
    uint8_t addr[4];
    memcpy(addr, &addr_be, 4);  // network order is the memory order of the address
    lock();
    const bool attempting = s_.connecting;
    if (!attempting) {
        s_.link_down = false;
    }
    unlock();
    if (attempting) {
        attempt_succeeded(addr);
    } else {
        draw_panel();
    }
}

void on_disconnected(void *, uint8_t reason)
{
    const setup_failure f = setup_failure_from_reason(reason);
    lock();
    if (s_.connecting) {
        // The reason this station asked to leave is not the network refusing the attempt:
        // the attempt's own connect disconnected the old link first.
        if (reason == kSetupReasonLeft) {
            unlock();
            return;
        }
        unlock();
        attempt_failed(f);
        return;
    }
    if (s_.open) {
        // The setup network is up and nothing is being tried: the panel already shows
        // the passphrase, and a station drop does not change that.
        unlock();
        return;
    }
    s_.link_down = true;
    s_.last_drop = f;
    unlock();
    draw_panel();
}

const net_listener s_listener = {nullptr, on_associated, on_address, on_disconnected};

// ---------------------------------------------------------------------------
// Closing the setup network
// ---------------------------------------------------------------------------

#if !CONFIG_RELAY_QEMU
void stop_dns();
#endif

void close_setup(void *)
{
    lock();
    if (!s_.open) {
        unlock();
        return;
    }
    s_.open = false;
    s_.recovery = false;
    s_.connecting = false;
    s_.connected = false;
    s_.failed = false;
    s_.link_down = false;
    s_.has_form = false;
    // Forget the passphrase with the network. A line of it in a log would otherwise
    // outlive the network it opened.
    memset(s_.passphrase, 0, sizeof s_.passphrase);
    unlock();

    esp_timer_stop(s_attempt_timer);
    esp_timer_stop(s_idle_timer);
#if !CONFIG_RELAY_QEMU
    if (s_http != nullptr) {
        httpd_stop(s_http);
        s_http = nullptr;
    }
    stop_dns();
#endif
    // The station reconnects by itself from here on, with the stored configuration.
    net_set_reconnect(true);
    net_radio_drop_ap();
    ESP_LOGI(TAG, "setup: setup network closed; the display keeps its settings");
    draw_panel();
}

void grace_elapsed(void *)
{
    close_setup(nullptr);
}

void idle_check(void *)
{
    lock();
    const bool idle = s_.open && s_.recovery && !s_.connecting &&
                      now_us() - s_.last_activity_us >= kRecoveryIdleUs;
    unlock();
    if (idle) {
        ESP_LOGI(TAG, "setup: no use for 15 minutes; returning to the stored network");
        close_setup(nullptr);
    }
}

// ---------------------------------------------------------------------------
// The setup page
// ---------------------------------------------------------------------------

#if !CONFIG_RELAY_QEMU

// Whether the request came from a client of the setup network. The page listens on every
// interface, because a station on the building's network may be up during recovery; it
// answers only the access point's own clients.
bool peer_is_setup_client(httpd_req_t *req)
{
    // Every refusal is logged: a silent 403 was undiagnosable on a real device.
    const int fd = httpd_req_to_sockfd(req);
    if (fd < 0) {
        ESP_LOGW(TAG, "setup: refused a request with no socket (fd %d)", fd);
        return false;
    }
    sockaddr_storage peer = {};
    socklen_t len = sizeof peer;
    if (getpeername(fd, reinterpret_cast<sockaddr *>(&peer), &len) != 0) {
        ESP_LOGW(TAG, "setup: refused a request, getpeername failed (errno %d)", errno);
        return false;
    }
    uint32_t host_order = 0;
    if (peer.ss_family == AF_INET) {
        host_order = ntohl(reinterpret_cast<const sockaddr_in *>(&peer)->sin_addr.s_addr);
    } else if (peer.ss_family == AF_INET6) {
        const auto *in6 = reinterpret_cast<const sockaddr_in6 *>(&peer);
        if (!setup_peer_from_mapped_v6(in6->sin6_addr.s6_addr, &host_order)) {
            ESP_LOGW(TAG, "setup: refused a request from an IPv6 address that is not IPv4-mapped");
            return false;
        }
    } else {
        ESP_LOGW(TAG, "setup: refused a request from address family %d", static_cast<int>(peer.ss_family));
        return false;
    }
    if (!setup_peer_allowed(host_order)) {
        ESP_LOGW(TAG, "setup: refused a request from %u.%u.%u.%u, not a client of the setup network",
                 static_cast<unsigned>(host_order >> 24), static_cast<unsigned>((host_order >> 16) & 0xFF),
                 static_cast<unsigned>((host_order >> 8) & 0xFF), static_cast<unsigned>(host_order & 0xFF));
        return false;
    }
    return true;
}

esp_err_t send_page(httpd_req_t *req, const page_view_t &view)
{
    const size_t n = setup_page_render(s_page, sizeof s_page, view);
    if (n == 0) {
        return httpd_resp_send_500(req);
    }
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "X-Frame-Options", "DENY");
    httpd_resp_set_hdr(req, "Content-Security-Policy", "frame-ancestors 'none'");
    return httpd_resp_send(req, s_page, static_cast<ssize_t>(n));
}

// What the page shows now. A page never shows the password: `page_view_t` has no field
// for it, and the form it borrows from is filled from the stored settings without it.
page_view_t view_now(setup_form_t *form_storage, char network[33], char address[16])
{
    page_view_t view;
    lock();
    memcpy(network, s_.network, 33);
    memcpy(address, s_.address, 16);
    if (s_.connected) {
        view.state = page_state::connected;
        view.network = network;
        view.address = address;
        view.close_in_seconds = static_cast<int>(kCloseGraceUs / 1000000);
    } else if (s_.connecting) {
        view.state = page_state::connecting;
        view.network = network;
    } else {
        view.state = page_state::form;
        view.notice = s_.failed ? s_.notice : nullptr;
        view.notice_is_error = s_.failed;
        if (s_.has_form) {
            *form_storage = s_.form;
        } else {
            setup_form_from_settings(s_.stored, form_storage);
        }
        view.form = form_storage;
    }
    unlock();
    return view;
}

#if CONFIG_RELAY_DEV_DIAG
// Who asked, and for what: the Host a phone typed or its captive-portal check sent, and its browser.
void diag_log_request(httpd_req_t *req, const char *what)
{
    char host[48] = "";
    char agent[96] = "";
    httpd_req_get_hdr_value_str(req, "Host", host, sizeof host);
    httpd_req_get_hdr_value_str(req, "User-Agent", agent, sizeof agent);
    ESP_LOGI(TAG, "diag: %s %s fd %d at %lld ms host '%s' agent '%s'", what, req->uri, httpd_req_to_sockfd(req),
             static_cast<long long>(esp_timer_get_time() / 1000), host, agent);
}
#endif

esp_err_t handle_root(httpd_req_t *req)
{
#if CONFIG_RELAY_DEV_DIAG
    diag_log_request(req, "GET");
#endif
#if CONFIG_RELAY_DEV_DIAG
    const int64_t t0 = DIAG_NOW();
#endif
    lock();
    s_.last_activity_us = now_us();
    unlock();
#if CONFIG_RELAY_DEV_DIAG
    const int64_t t_lock = DIAG_NOW();
#endif
    if (!peer_is_setup_client(req)) {
        return httpd_resp_send_err(req, HTTPD_403_FORBIDDEN,
                                   "This page is served on the display's own setup network only.");
    }
    setup_form_t form;
    char network[33];
    char address[16];
    const page_view_t view = view_now(&form, network, address);
#if CONFIG_RELAY_DEV_DIAG
    const int64_t t_view = DIAG_NOW();
#endif
    const esp_err_t err = send_page(req, view);
#if CONFIG_RELAY_DEV_DIAG
    // Phases of one GET /: waiting for the setup state's lock, building the view, and
    // rendering and sending the page. A slow page is slow in exactly one of them.
    ESP_LOGI(TAG, "diag: GET / lock %lld ms, view %lld ms, send %lld ms (err %d)",
             static_cast<long long>((t_lock - t0) / 1000), static_cast<long long>((t_view - t_lock) / 1000),
             static_cast<long long>(diag_ms_since(t_view)), static_cast<int>(err));
#endif
    return err;
}

// Read the whole body into `s_body`, NUL-terminated. False when it cannot be read.
bool read_body(httpd_req_t *req, size_t total)
{
    size_t got = 0;
    while (got < total) {
        const int r = httpd_req_recv(req, s_body + got, total - got);
        if (r <= 0) {
            return false;
        }
        got += static_cast<size_t>(r);
    }
    s_body[total] = '\0';
    return true;
}

esp_err_t handle_configure(httpd_req_t *req)
{
    lock();
    s_.last_activity_us = now_us();
    unlock();
    if (!peer_is_setup_client(req)) {
        return httpd_resp_send_err(req, HTTPD_403_FORBIDDEN,
                                   "This page is served on the display's own setup network only.");
    }

    // The Origin check. A browser always sends Origin on a form post, so one from
    // another site is refused, and a request with none (a script on the setup network) is
    // allowed. The page is plain HTTP, so the scheme is `http://`, not the TLS one.
    char host[128] = {0};
    char origin[160] = {0};
    const bool have_host = httpd_req_get_hdr_value_str(req, "Host", host, sizeof host) == ESP_OK;
    if (!have_host) {
        host[0] = '\0';
    }
    const bool have_origin = httpd_req_get_hdr_value_len(req, "Origin") > 0 &&
                             httpd_req_get_hdr_value_str(req, "Origin", origin, sizeof origin) == ESP_OK;
    if (!origin_allowed_for("http://", have_origin ? origin : nullptr, host)) {
        ESP_LOGW(TAG, "setup: refused a form post from another origin");
        return httpd_resp_send_err(req, HTTPD_403_FORBIDDEN, "The form must be sent from the setup page.");
    }

    if (req->content_len <= 0 || static_cast<size_t>(req->content_len) > kBodyMax) {
        return httpd_resp_send_err(req, HTTPD_413_CONTENT_TOO_LARGE, "The form is too large.");
    }
    if (!read_body(req, static_cast<size_t>(req->content_len))) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "The form could not be read.");
    }

    lock();
    const bool busy = s_.connecting;
    unlock();
    if (busy) {
        // One attempt at a time. A second submit shows the attempt that is running.
        char network[33];
        lock();
        memcpy(network, s_.network, sizeof network);
        unlock();
        page_view_t view;
        view.state = page_state::connecting;
        view.network = network;
        return send_page(req, view);
    }

    // The form is parsed and checked here, before the radio is touched. A refusal changes
    // nothing: the stored settings, the station and the attempt state are all as they were.
    setup_form_t form;
    const char *problem = setup_form_parse(s_body, &form);
    // The body held the password. Nothing after this point reads it except the candidate.
    memset(s_body, 0, sizeof s_body);
    if (problem != nullptr) {
        ESP_LOGW(TAG, "setup: form refused: %s", problem);
        lock();
        s_.form = form;
        s_.has_form = true;
        s_.failed = true;
        s_.failure = setup_failure::none;
        s_.notice = problem;
        unlock();
        draw_panel();
        page_view_t view;
        view.notice = problem;
        view.notice_is_error = true;
        view.form = &form;
        return send_page(req, view);
    }

    lock();
    s_.form = form;
    s_.has_form = true;
    s_.candidate = s_.stored;
    setup_form_apply(&s_.candidate, form);
    s_.connecting = true;
    s_.associated = false;
    s_.connected = false;
    s_.failed = false;
    s_.failure = setup_failure::none;
    s_.notice = nullptr;
    memcpy(s_.network, form.ssid, sizeof s_.network);
    const net_sta_config sta = sta_from_settings(s_.candidate);
    unlock();

    ESP_LOGI(TAG, "setup: trying SSID '%s' (%s)", sta.ssid, sta.ip.dhcp ? "DHCP" : "static address");
    esp_timer_stop(s_attempt_timer);
    esp_timer_start_once(s_attempt_timer, kAttemptTimeoutUs);
    if (net_station_connect(&sta) != ESP_OK) {
        attempt_failed(setup_failure::could_not_start);
    } else {
        draw_panel();
    }

    char network[33];
    lock();
    memcpy(network, s_.network, sizeof network);
    unlock();
    page_view_t view;
    view.state = page_state::connecting;
    view.network = network;
    return send_page(req, view);
}

// The setup network's resolver (`setup_dns_answer` says why it exists). One task, bound to the access point's
// own address, so it never answers on the building's network; it answers only the setup network's clients, as the
// page does, and only while the setup network is open. It polls its flag every half second, which is how long a
// close waits for the port to be free again.
std::atomic<bool> s_dns_run{false};
TaskHandle_t s_dns_task = nullptr;

void dns_task(void *)
{
    const int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    sockaddr_in local = {};
    local.sin_family = AF_INET;
    local.sin_port = htons(53);
    local.sin_addr.s_addr = htonl(kSetupHostAddress);
    const timeval wait = {0, 500 * 1000};
    if (fd < 0 || bind(fd, reinterpret_cast<sockaddr *>(&local), sizeof local) != 0 ||
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &wait, sizeof wait) != 0) {
        ESP_LOGE(TAG, "setup: the setup network's DNS did not start (errno %d); a phone will wait on its lookups",
                 errno);
    } else {
        ESP_LOGI(TAG, "setup: DNS answers every name with the panel's address");
        uint8_t query[512];
        uint8_t reply[512];
        while (s_dns_run.load()) {
            sockaddr_in peer = {};
            socklen_t peer_len = sizeof peer;
            const int n = recvfrom(fd, query, sizeof query, 0, reinterpret_cast<sockaddr *>(&peer), &peer_len);
            if (n <= 0 || peer.sin_family != AF_INET || !setup_peer_allowed(ntohl(peer.sin_addr.s_addr))) {
                continue;
            }
            const size_t len = setup_dns_answer(query, static_cast<size_t>(n), kSetupHostAddress, reply, sizeof reply);
            if (len > 0) {
                sendto(fd, reply, len, 0, reinterpret_cast<sockaddr *>(&peer), peer_len);
            }
        }
    }
    if (fd >= 0) {
        close(fd);
    }
    s_dns_task = nullptr;
    vTaskDelete(nullptr);
}

void start_dns()
{
    // A close a moment ago may still hold the port for up to half a second.
    for (int i = 0; i < 20 && s_dns_task != nullptr; ++i) {
        vTaskDelay(pdMS_TO_TICKS(50));
    }
    if (s_dns_task != nullptr) {
        ESP_LOGW(TAG, "setup: the last DNS task has not ended; this setup network has no DNS");
        return;
    }
    s_dns_run.store(true);
    if (xTaskCreate(dns_task, "setup_dns", 4096, nullptr, 5, &s_dns_task) != pdPASS) {
        s_dns_run.store(false);
        s_dns_task = nullptr;
        ESP_LOGE(TAG, "setup: no memory for the setup network's DNS task");
    }
}

void stop_dns()
{
    s_dns_run.store(false);
}

// **Any other path is sent to the setup page.** With every name resolving to the panel, a phone's captive-portal
// check asks for its own path here (`/hotspot-detect.html` on iOS, `/generate_204` on Android). A redirect is the
// answer that makes the phone show a sign-in sheet, and the page is in it. The destination is a constant.
esp_err_t not_found_to_page(httpd_req_t *req, httpd_err_code_t)
{
#if CONFIG_RELAY_DEV_DIAG
    diag_log_request(req, "redirect");
#endif
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", kSetupPageAddress);
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, nullptr, 0);
}

esp_err_t start_http()
{
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.max_uri_handlers = 4;
    config.stack_size = 8192;
    config.lru_purge_enable = true;
#if CONFIG_RELAY_DEV_DIAG
    // Each connection's accept and close, with the time: a phone that opens five sockets and
    // uses one shows here, and so does a gap between a join and the first accept.
    config.open_fn = [](httpd_handle_t, int fd) -> esp_err_t {
        ESP_LOGI(TAG, "diag: open fd %d at %lld ms", fd, static_cast<long long>(esp_timer_get_time() / 1000));
        return ESP_OK;
    };
    config.close_fn = [](httpd_handle_t, int fd) {
        ESP_LOGI(TAG, "diag: close fd %d at %lld ms", fd, static_cast<long long>(esp_timer_get_time() / 1000));
        close(fd);
    };
#endif
    ESP_RETURN_ON_ERROR(httpd_start(&s_http, &config), TAG, "setup page");

    const httpd_uri_t root = {"/", HTTP_GET, handle_root, nullptr};
    const httpd_uri_t configure = {"/configure", HTTP_POST, handle_configure, nullptr};
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(s_http, &root), TAG, "setup page route");
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(s_http, &configure), TAG, "setup form route");
    ESP_RETURN_ON_ERROR(httpd_register_err_handler(s_http, HTTPD_404_NOT_FOUND, not_found_to_page), TAG,
                        "setup page redirect");
    start_dns();
    return ESP_OK;
}

#endif  // !CONFIG_RELAY_QEMU

// ---------------------------------------------------------------------------
// Recovery
// ---------------------------------------------------------------------------

void console_task(void *)
{
    // `recover` on the serial console is one of two recovery triggers. The other is a five-second hold of BOOT
    //, which calls the same function from its own task. The console stays: it recovers without
    // touching the button, and it reads the clock for a bench check. The console is the USB port the board is flashed
    // through, so typing into it needs the same physical access as a reflash, which already reads every stored value
    // (README, "Recovery").
    char line[192];
    for (;;) {
        if (fgets(line, sizeof line, stdin) == nullptr) {
            clearerr(stdin);
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }
        size_t n = strlen(line);
        while (n > 0 && (line[n - 1] == '\n' || line[n - 1] == '\r')) {
            line[--n] = '\0';
        }
        if (strcmp(line, "recover") == 0) {
            ESP_LOGW(TAG, "recovery: opening the setup network from the serial console");
            provision_open_setup(true);
        } else if (strcmp(line, "id") == 0) {
            // The panel ID names this panel to Relay. It is not a secret, and an installer matches it against the
            // admin page's list of panels.
            lock();
            ESP_LOGI(TAG, "console: panel id %s", s_.stored.panel_id);
            unlock();
        } else if (strncmp(line, "feed ", 5) == 0) {
            // Point a caption slot at a Relay, for the bench and the emulator suite. Not saved: the stored feed is
            // what the panel shows after a restart.
            //   feed <slot> off | feed <slot> <url> transcription | feed <slot> <url> translation <LANG>
            feeds_go_manual();
            int slot = -1;
            char url[130] = "";
            char stream[16] = "";
            char lang[20] = "";
            const int got = sscanf(line + 5, "%d %129s %15s %19s", &slot, url, stream, lang);
            esp_err_t err = ESP_ERR_INVALID_ARG;
            if (got == 2 && strcmp(url, "off") == 0) {
                err = captions_set_feed(slot, "", kFeedNone, "");
            } else if (got == 3 && strcmp(stream, "transcription") == 0) {
                err = captions_set_feed(slot, url, kFeedTranscription, "");
            } else if (got == 4 && strcmp(stream, "translation") == 0) {
                err = captions_set_feed(slot, url, kFeedTranslation, lang);
            }
            ESP_LOGI(TAG, "console: feed %s", err == ESP_OK ? "set" : "not set: feed <slot> off | <url> transcription | <url> translation <LANG>");
            // Slot 0 is what the screen shows: a feed puts the captions up, and no feed puts the bench picture back.
            if (err == ESP_OK && slot == 0) {
                if (got == 2) {
                    screen_show_bench(RELAY_FW_VERSION);
                } else {
                    screen_show_captions();
                }
            }
        } else if (strncmp(line, "relay ", 6) == 0) {
            // Point the feeds at a Relay for the bench and the emulator suite, and let them follow the choice again
            // (what `feed` took from them). Not saved.
            feeds_use_relay(line + 6);
            ESP_LOGI(TAG, "console: relay set");
        } else if (strcmp(line, "feeds") == 0) {
            feeds_report();
        } else if (strcmp(line, "panel") == 0) {
            panel_link_report();
        } else if (strncmp(line, "captions ", 9) == 0) {
            captions_dump(atoi(line + 9));
        } else if (strcmp(line, "view") == 0) {
            caption_view_report();
#if CONFIG_RELAY_DEV_DIAG
        } else if (strncmp(line, "pclk ", 5) == 0) {
            // A bench build only: try a pixel clock without a reflash. The frame rate in the next diag block shows
            // what the panel achieved.
            const unsigned long hz = strtoul(line + 5, nullptr, 10);
            const esp_err_t err = hz >= 8000000 && hz <= 40000000 ? display_set_pclk(static_cast<uint32_t>(hz))
                                                                  : ESP_ERR_INVALID_ARG;
            ESP_LOGW(TAG, "console: pclk %lu Hz: %s", hz, esp_err_to_name(err));
        } else if (strncmp(line, "diag ", 5) == 0) {
            // A bench build only: which part of the five-second diag block the panel feels.
            ESP_LOGW(TAG, "console: diag %s: %s", line + 5, diag_set_mode(line + 5) ? "set" : "unknown mode");
        } else if (strcmp(line, "resync") == 0) {
            display_resync();
            ESP_LOGW(TAG, "console: panel DMA restart requested");
#endif
#if CONFIG_RELAY_QEMU
        } else if (strcmp(line, "touch up") == 0) {
            // The emulator has no touch controller: the suite puts a finger down and lifts it from here.
            touch_inject(0, 0, false);
        } else if (strncmp(line, "touch ", 6) == 0) {
            int x = 0;
            int y = 0;
            if (sscanf(line + 6, "%d %d", &x, &y) == 2) {
                touch_inject(x, y, true);
            }
#endif
        } else if (n > 0) {
            ESP_LOGI(TAG, "console: type 'recover' and press Enter to open the setup network, or 'id' for the panel ID");
        }
    }
}

void ensure_ready()
{
    if (s_ready) {
        return;
    }
    s_lock = xSemaphoreCreateMutex();
    s_paint_lock = xSemaphoreCreateMutex();
    s_open_lock = xSemaphoreCreateMutex();
    const esp_timer_create_args_t attempt = {
        .callback = attempt_timeout, .arg = nullptr, .dispatch_method = ESP_TIMER_TASK,
        .name = "setup_attempt", .skip_unhandled_events = false};
    const esp_timer_create_args_t close = {
        .callback = grace_elapsed, .arg = nullptr, .dispatch_method = ESP_TIMER_TASK,
        .name = "setup_close", .skip_unhandled_events = false};
    const esp_timer_create_args_t idle = {
        .callback = idle_check, .arg = nullptr, .dispatch_method = ESP_TIMER_TASK,
        .name = "setup_idle", .skip_unhandled_events = false};
    esp_timer_create(&attempt, &s_attempt_timer);
    esp_timer_create(&close, &s_close_timer);
    esp_timer_create(&idle, &s_idle_timer);
    s_ready = true;
}

}  // namespace

esp_err_t provision_start(const relay_settings_t &settings)
{
    ensure_ready();
    lock();
    s_.stored = settings;
    unlock();

    // The console is started once, whatever the boot decides, so recovery is there from
    // the first minute.
    xTaskCreate(console_task, "setup_console", 4096, nullptr, 3, nullptr);

    if (wifi_should_start(settings.wifi_ssid)) {
        ESP_RETURN_ON_ERROR(net_init(), TAG, "net init");
        net_set_listener(&s_listener);
        net_set_reconnect(true);
        const net_sta_config sta = sta_from_settings(settings);
        ESP_LOGI(TAG, "boot: a network is configured; joining it, setup network not opened");
        return net_radio_start(nullptr, &sta);
    }
    return provision_open_setup(false);
}

esp_err_t provision_open_setup(bool recovery)
{
    ensure_ready();
    // Taken before anything else, so a second caller waits here and then finds the network open.
    OpenLock open_once;
    lock();
    if (s_.open) {
        unlock();
        ESP_LOGI(TAG, "setup: the setup network is already open");
        return ESP_OK;
    }
    const relay_settings_t stored = s_.stored;
    unlock();

    // A new passphrase every time the network opens. It is shown on the panel and
    // never logged, and it is forgotten when the network closes.
    char passphrase[kSetupPassphraseLength + 1];
    generate_passphrase(passphrase);
    uint8_t mac[6] = {0};
    ESP_RETURN_ON_ERROR(esp_read_mac(mac, ESP_MAC_WIFI_STA), TAG, "MAC");
    char ap_ssid[17];
    setup_ssid_for_mac(mac, ap_ssid);

    net_ap_config ap = {};
    copy_name(ap.ssid, sizeof ap.ssid, ap_ssid);
    copy_name(ap.password, sizeof ap.password, passphrase);

    ESP_RETURN_ON_ERROR(net_init(), TAG, "net init");
    net_set_listener(&s_listener);
    // The setup network is not the place to retry a configuration that has just been
    // refused, so the station does not retry in the background while it is open.
    net_set_reconnect(false);

    // Recovery keeps the stored network, so the station keeps joining it while the setup
    // network is open. A first boot with nothing stored has no station.
    net_sta_config sta_cfg = {};
    const net_sta_config *sta = nullptr;
    const bool has_stored = wifi_should_start(stored.wifi_ssid);
    if (recovery && has_stored) {
        sta_cfg = sta_from_settings(stored);
        sta = &sta_cfg;
    }
    ESP_RETURN_ON_ERROR(net_radio_start(&ap, sta), TAG, "setup network");

#if !CONFIG_RELAY_QEMU
    const esp_err_t http = start_http();
    if (http != ESP_OK) {
        ESP_LOGE(TAG, "setup: the setup page did not start (%s)", esp_err_to_name(http));
        return http;
    }
#else
    ESP_LOGW(TAG, "provision: setup page skipped under emulation (QEMU has no radio)");
#endif

    lock();
    s_.open = true;
    s_.recovery = recovery && has_stored;
    s_.connecting = false;
    s_.connected = false;
    s_.failed = false;
    s_.failure = setup_failure::none;
    s_.notice = nullptr;
    s_.has_form = false;
    s_.link_down = false;
    s_.last_activity_us = now_us();
    memcpy(s_.passphrase, passphrase, sizeof s_.passphrase);
    memcpy(s_.ap_ssid, ap_ssid, sizeof s_.ap_ssid);
    unlock();

    // The name is logged, and the passphrase is not. The name is what an installer matches
    // against the label, and it is public: any phone sees it.
    ESP_LOGI(TAG, "setup: network '%s' is up; it is protected by the passphrase on the panel", ap_ssid);
    if (s_.recovery) {
        esp_timer_start_periodic(s_idle_timer, kIdleCheckUs);
    }
    draw_panel();
    return ESP_OK;
}

bool provision_setup_open()
{
    ensure_ready();
    lock();
    const bool open = s_.open;
    unlock();
    return open;
}

void provision_stored_settings(relay_settings_t *out)
{
    ensure_ready();
    lock();
    *out = s_.stored;
    unlock();
}
