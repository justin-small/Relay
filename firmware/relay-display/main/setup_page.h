#pragma once

// The setup page's HTML, built with no ESP-IDF in it. `provision.cpp` serves
// the bytes this writes; `test_native.cpp` checks what they say.
//
// **The page never shows the Wi-Fi password, the access point's passphrase or any
// stored value it was not given to show.** The password field is written without a
// value, so a stored password cannot come back in a page, and the function that
// writes the form takes no password at all. Every value that is echoed is escaped.

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "setup_format.h"

enum class page_state {
    form,        // the form, with an error above it if there is one
    connecting,  // an attempt is running; the page refreshes until it finishes
    connected,   // the attempt worked and the settings are saved
};

struct page_view_t {
    page_state state = page_state::form;
    const char *notice = nullptr;     // an error or a status line, escaped when written
    bool notice_is_error = false;
    const setup_form_t *form = nullptr;  // what to show in the fields; nullptr for blanks
    const char *network = nullptr;    // the network name for the connecting and connected states
    const char *address = nullptr;    // the address the station got, for connected
    int close_in_seconds = 0;         // for connected: when the setup network closes
};

namespace setup_page_detail {

// Append `s` to `out` with the characters HTML would read as markup escaped. Returns
// false when it does not fit; `*len` then holds what was written before the failure.
inline bool append_escaped(char *out, size_t cap, size_t *len, const char *s)
{
    for (const char *p = s; *p != '\0'; ++p) {
        const char *rep = nullptr;
        switch (*p) {
        case '&': rep = "&amp;"; break;
        case '<': rep = "&lt;"; break;
        case '>': rep = "&gt;"; break;
        case '"': rep = "&quot;"; break;
        case '\'': rep = "&#39;"; break;
        default: break;
        }
        const size_t n = rep != nullptr ? strlen(rep) : 1;
        if (*len + n + 1 > cap) {
            return false;
        }
        if (rep != nullptr) {
            memcpy(out + *len, rep, n);
        } else {
            out[*len] = *p;
        }
        *len += n;
    }
    out[*len] = '\0';
    return true;
}

inline bool append_raw(char *out, size_t cap, size_t *len, const char *s)
{
    const size_t n = strlen(s);
    if (*len + n + 1 > cap) {
        return false;
    }
    memcpy(out + *len, s, n);
    *len += n;
    out[*len] = '\0';
    return true;
}

// A text input. `value` is escaped; the caller never passes a secret here.
inline bool text_input(char *out, size_t cap, size_t *len, const char *label, const char *name,
                       const char *value, const char *extra)
{
    return append_raw(out, cap, len, "<label>") && append_escaped(out, cap, len, label) &&
           append_raw(out, cap, len, "<input name=\"") && append_raw(out, cap, len, name) &&
           append_raw(out, cap, len, "\" ") && append_raw(out, cap, len, extra) && append_raw(out, cap, len, " value=\"") &&
           append_escaped(out, cap, len, value != nullptr ? value : "") && append_raw(out, cap, len, "\"></label>");
}

}  // namespace setup_page_detail

// Write the page for `v` into `out`, NUL-terminated. Returns the length, or 0 when the
// page did not fit `cap`. The size needed is well under 4 KB, so a caller that gives
// it 6 KB never sees 0.
inline size_t setup_page_render(char *out, size_t cap, const page_view_t &v)
{
    using namespace setup_page_detail;
    size_t len = 0;
    bool ok = append_raw(out, cap, &len,
                         "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
                         "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                         "<title>Relay panel setup</title>");
    if (v.state == page_state::connecting) {
        // **Refreshes to this same page**, so the result shows without a button. The
        // refresh is to `/`, not to a URL built from anything the request sent.
        ok = ok && append_raw(out, cap, &len, "<meta http-equiv=\"refresh\" content=\"2;url=/\">");
    }
    ok = ok && append_raw(out, cap, &len,
                          "<style>"
                          "body{font-family:system-ui,sans-serif;margin:0;padding:16px;color:#111;background:#fff;max-width:36rem}"
                          "h1{font-size:1.4rem}"
                          "label{display:block;margin:12px 0 4px;font-weight:600}"
                          "input{width:100%;box-sizing:border-box;font:inherit;padding:8px;border:1px solid;border-radius:4px}"
                          "fieldset{border:1px solid #bbb;border-radius:4px;margin:16px 0;padding:8px 12px}"
                          "legend{font-weight:600}"
                          "button{font:inherit;padding:10px 16px;margin-top:16px;border:0;border-radius:4px;background:#1a4fa0;color:#fff}"
                          ".notice{padding:10px;border-radius:4px;margin:12px 0;border:1px solid}"
                          ".error{background:#fde8e8;border-color:#b00020}"
                          ".ok{background:#e8f5e9;border-color:#2e7d32}"
                          ".hint{color:#444;font-size:0.9rem}"
                          "</style></head><body><h1>Relay panel setup</h1>");

    if (ok && v.state == page_state::connecting) {
        ok = append_raw(out, cap, &len, "<p class=\"notice ok\">Connecting to ") &&
             append_escaped(out, cap, &len, v.network != nullptr ? v.network : "the network") &&
             append_raw(out, cap, &len, "&hellip; This page updates when it is done.</p>");
    } else if (ok && v.state == page_state::connected) {
        ok = append_raw(out, cap, &len, "<p class=\"notice ok\">Connected to ") &&
             append_escaped(out, cap, &len, v.network != nullptr ? v.network : "the network") &&
             append_raw(out, cap, &len, ". The display's address is ") &&
             append_escaped(out, cap, &len, v.address != nullptr ? v.address : "not known") &&
             append_raw(out, cap, &len, ". This setup network closes in about ");
        char seconds[16];
        snprintf(seconds, sizeof seconds, "%d", v.close_in_seconds);
        ok = ok && append_raw(out, cap, &len, seconds) && append_raw(out, cap, &len, " seconds.</p>");
    } else if (ok) {
        if (v.notice != nullptr && v.notice[0] != '\0') {
            ok = append_raw(out, cap, &len, v.notice_is_error ? "<p class=\"notice error\">" : "<p class=\"notice ok\">") &&
                 append_escaped(out, cap, &len, v.notice) && append_raw(out, cap, &len, "</p>");
        }
        const setup_form_t *f = v.form;
        const bool dhcp = f == nullptr || f->dhcp;
        ok = ok && append_raw(out, cap, &len,
                              "<form method=\"post\" action=\"/configure\" autocomplete=\"off\">"
                              "<p class=\"hint\">The Wi-Fi password is not shown here. Leave it empty to keep an open network.</p>");
        ok = ok && text_input(out, cap, &len, "Wi-Fi network name", "ssid", f != nullptr ? f->ssid : "",
                              "maxlength=\"32\" required");
        ok = ok && append_raw(out, cap, &len, "<label>Wi-Fi password<input type=\"password\" name=\"pass\" "
                                              "maxlength=\"63\" autocomplete=\"new-password\"></label>");
        ok = ok && append_raw(out, cap, &len, "<fieldset><legend>Address</legend>"
                                              "<label><input type=\"radio\" name=\"mode\" value=\"dhcp\"");
        ok = ok && append_raw(out, cap, &len, dhcp ? " checked" : "");
        ok = ok && append_raw(out, cap, &len, "> Get the address automatically (DHCP)</label>"
                                              "<label><input type=\"radio\" name=\"mode\" value=\"static\"");
        ok = ok && append_raw(out, cap, &len, dhcp ? "" : " checked");
        ok = ok && append_raw(out, cap, &len, "> Use a fixed address</label>");
        ok = ok && text_input(out, cap, &len, "IP address", "ip", f != nullptr ? f->ip_text : "", "placeholder=\"192.168.1.50\"");
        ok = ok && text_input(out, cap, &len, "Subnet mask", "mask", f != nullptr ? f->mask_text : "", "placeholder=\"255.255.255.0\"");
        ok = ok && text_input(out, cap, &len, "Default gateway", "gw", f != nullptr ? f->gateway_text : "", "placeholder=\"192.168.1.1\"");
        ok = ok && text_input(out, cap, &len, "Primary DNS", "dns1", f != nullptr ? f->dns1_text : "", "placeholder=\"192.168.1.1\"");
        ok = ok && text_input(out, cap, &len, "Secondary DNS (optional)", "dns2", f != nullptr ? f->dns2_text : "", "");
        ok = ok && append_raw(out, cap, &len, "</fieldset>");
        ok = ok && text_input(out, cap, &len, "Relay address (optional)", "relay", f != nullptr ? f->relay_text : "",
                              "maxlength=\"159\" placeholder=\"192.168.1.20:8000\"");
        ok = ok && append_raw(out, cap, &len, "<p class=\"hint\">Leave empty and the panel finds Relay on this "
                                              "network. Fill it in only if Relay is somewhere the panel cannot "
                                              "find it: a host or IPv4 address, with a port if it has one.</p>"
                                              "<button type=\"submit\">Save and connect</button></form>");
    }
    ok = ok && append_raw(out, cap, &len, "</body></html>");
    return ok ? len : 0;
}
