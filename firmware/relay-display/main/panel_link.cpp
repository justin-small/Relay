#include "panel_link.h"

#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <new>

#include "captions.h"
#include "config.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "feeds.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "net.h"
#include "panel_link_format.h"
#include "sdkconfig.h"

namespace {

const char *TAG = "panel_link";

constexpr int64_t kSilenceUs = static_cast<int64_t>(CONFIG_RELAY_CAPTION_SILENCE_S) * 1000 * 1000;
constexpr size_t kSseBytes = 2048;

struct State {
    char id[33] = {};
    TaskHandle_t checkin = nullptr;
    // The last operator feed applied, which the next check-in reports so Relay stops sending it.
    std::atomic<uint32_t> applied_rev{0};
    // Relay has answered a check-in since the stream last failed: it knows this panel, so the stream may open.
    std::atomic<bool> registered{false};
    std::atomic<uint32_t> checkins{0};
    std::atomic<bool> stream_open{false};
    char name[kPanelNameMax] = {};  // what Relay calls this panel; the check-in task's
};

State s_;
bool s_started = false;

// The check-in task's own. Static: the stack is small, and the feeds snapshot is a kilobyte or two.
feeds_view s_view;
char s_body[kPanelBodyMax];
char s_request[kPanelBodyMax + 320];
char s_reply[kPanelReplyMax];

uint32_t jitter_ms(uint32_t span)
{
    return esp_random() % span;
}

bool relay_address(char *url, size_t cap)
{
    return feeds_relay_url(url, cap) && captions_has_address();
}

void apply(const panel_set_feed &f, const char *how)
{
    if (s_.applied_rev.exchange(f.rev) == f.rev) {
        return;  // already applied: the same one reaches the panel in a check-in reply and on the stream
    }
    feeds_apply(f.feeds, f.count);
    ESP_LOGI(TAG, "panel_link: set_feed rev %u applied (%d feed%s, %s)", static_cast<unsigned>(f.rev), f.count,
             f.count == 1 ? "" : "s", how);
}

// ---------------------------------------------------------------------------
// The check-in
// ---------------------------------------------------------------------------

struct Body {
    char *out;
    size_t cap;
    size_t len;
};

void on_reply_body(void *ctx, const char *p, size_t n)
{
    auto *b = static_cast<Body *>(ctx);
    if (b->len + n + 1 > b->cap) {
        n = b->cap - b->len - 1;  // a reply cut here fails to parse, and the check-in counts as failed
    }
    memcpy(b->out + b->len, p, n);
    b->len += n;
    b->out[b->len] = '\0';
}

// One POST. True when Relay answered 200 and accepted the check-in.
bool checkin(const char *url)
{
    char host[sizeof(relay_settings_t::relay_addr)];
    uint16_t port = 0;
    if (!caption_url_split(url, host, sizeof host, &port)) {
        return false;
    }
    panel_status st = {};
    snprintf(st.id, sizeof st.id, "%s", s_.id);
    snprintf(st.version, sizeof st.version, "%s", RELAY_FW_VERSION);
    st.uptime_s = static_cast<uint32_t>(esp_timer_get_time() / 1000000);
    st.feed_rev = s_.applied_rev.load();
    int rssi = 0;
    st.has_rssi = net_link_info(st.ip, sizeof st.ip, &rssi);
    st.rssi = rssi;
    feeds_snapshot(&s_view);
    for (int pane = 0; pane < kPanelFeedsMax && pane < (s_view.split ? 2 : 1); ++pane) {
        if (s_view.shown[pane].pick.stream != kFeedNone) {
            st.feeds[st.feed_count++] = s_view.shown[pane].pick;
        }
    }
    const size_t body_len = panel_checkin_body(s_body, sizeof s_body, st);
    const size_t request_len =
        body_len > 0 ? panel_checkin_request(s_request, sizeof s_request, host, port, s_body, body_len, RELAY_FW_VERSION)
                     : 0;
    if (request_len == 0) {
        ESP_LOGE(TAG, "panel_link: the check-in does not fit");
        return false;
    }
    const int fd = captions_connect(host, port);
    if (fd < 0) {
        return false;
    }
    auto *http = new (std::nothrow) caption_http();
    Body reply = {s_reply, sizeof s_reply, 0};
    s_reply[0] = '\0';
    bool ok = false;
    if (http != nullptr && captions_send(fd, s_request, request_len)) {
        char buf[512];
        const int64_t deadline = esp_timer_get_time() + 8 * 1000 * 1000;
        while (esp_timer_get_time() < deadline) {
            const ssize_t n = recv(fd, buf, sizeof buf, 0);
            if (n > 0) {
                if (!http->feed(buf, static_cast<size_t>(n), on_reply_body, &reply) ||
                    http->state() == caption_http::phase::done) {
                    break;
                }
                continue;
            }
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                continue;
            }
            break;  // closed: the body is whole (Connection: close)
        }
        if (http->status() == 200) {
            const panel_reply r = panel_parse_reply(s_reply, reply.len);
            ok = r.ok;
            if (ok) {
                if (strcmp(r.name, s_.name) != 0) {
                    snprintf(s_.name, sizeof s_.name, "%s", r.name);
                    ESP_LOGI(TAG, "panel_link: Relay calls this panel \"%s\"", s_.name);
                }
                if (r.has_feed && panel_should_apply(r.feed.rev, s_.applied_rev.load())) {
                    apply(r.feed, "check-in");
                }
            }
        } else if (http->status() != 0) {
            ESP_LOGW(TAG, "panel_link: Relay answered the check-in with %d", http->status());
        }
    }
    delete http;
    shutdown(fd, SHUT_RDWR);
    close(fd);
    return ok;
}

void checkin_task(void *)
{
    bool was_ok = false;
    for (;;) {
        char url[sizeof(relay_settings_t::relay_addr)];
        bool ok = false;
        if (relay_address(url, sizeof url)) {
            ok = checkin(url);
            if (ok != was_ok) {
                if (ok) {
                    ESP_LOGI(TAG, "panel_link: checked in with Relay");
                } else {
                    ESP_LOGI(TAG, "panel_link: Relay did not take the check-in");
                }
                was_ok = ok;
            }
            if (ok) {
                s_.checkins.fetch_add(1);
            }
        }
        s_.registered = ok;
        // Woken early when the stream is told Relay does not know this panel.
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS((ok ? kPanelCheckinMs : kPanelCheckinRetryMs) + jitter_ms(2000)));
    }
}

// ---------------------------------------------------------------------------
// The event stream
// ---------------------------------------------------------------------------

struct Stream {
    caption_http http;
    caption_sse sse;
    bool opened = false;
    bool rejected = false;
    int64_t opened_at = 0;
    Stream(char *buf, size_t cap) : sse(buf, cap) {}
};

// The response's headers are in: is this an event stream? Called before any body byte is used, so a frame that comes
// in the same packet as the headers (the `set_feed` Relay writes on connect) is not lost.
void check_open(Stream &s)
{
    if (s.opened || s.rejected || s.http.state() != caption_http::phase::body) {
        return;
    }
    if (s.http.status() != 200 || !s.http.event_stream()) {
        s.rejected = true;
        return;
    }
    s.opened = true;
    s.opened_at = esp_timer_get_time();
    s_.stream_open = true;
    ESP_LOGI(TAG, "panel_link: event stream open");
}

void on_event(void *, const caption_sse_event &event)
{
    if (event.oversize) {
        return;
    }
    const panel_event ev = panel_parse_event(event.data, event.len);
    switch (ev.kind) {
    case panel_message::set_feed:
        apply(ev.feed, "stream");
        break;
    case panel_message::update_available:
        // The update itself is a later piece of work; the panel says it heard.
        ESP_LOGI(TAG, "panel_link: update available%s%s", ev.version[0] != '\0' ? ": " : "", ev.version);
        break;
    case panel_message::none:
        break;
    }
}

void on_comment(void *, const char *) {}

void on_stream_body(void *ctx, const char *p, size_t n)
{
    auto *s = static_cast<Stream *>(ctx);
    check_open(*s);
    if (s->opened) {
        s->sse.feed(p, n, on_event, on_comment, s);
    }
}

// One stream, until it ends. Returns the HTTP status Relay answered (0 when it never did).
int run_stream(const char *url, char *sse_buf, uint32_t *open_ms)
{
    *open_ms = 0;
    char host[sizeof(relay_settings_t::relay_addr)];
    uint16_t port = 0;
    char request[320];
    if (!caption_url_split(url, host, sizeof host, &port) ||
        panel_events_request(request, sizeof request, host, port, s_.id, RELAY_FW_VERSION) == 0) {
        return 0;
    }
    const int fd = captions_connect(host, port);
    if (fd < 0 || !captions_send(fd, request, strlen(request))) {
        if (fd >= 0) {
            close(fd);
        }
        return 0;
    }
    auto *stream = new (std::nothrow) Stream(sse_buf, kSseBytes);
    if (stream == nullptr) {
        close(fd);
        return 0;
    }
    char buf[512];
    int64_t last_rx = esp_timer_get_time();
    for (;;) {
        const ssize_t n = recv(fd, buf, sizeof buf, 0);
        const int64_t now = esp_timer_get_time();
        if (n > 0) {
            last_rx = now;
            if (!stream->http.feed(buf, static_cast<size_t>(n), on_stream_body, stream)) {
                break;
            }
            check_open(*stream);
            if (stream->rejected) {
                break;
            }
            if (stream->http.state() == caption_http::phase::done) {
                break;
            }
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            if (now - last_rx > kSilenceUs) {
                ESP_LOGW(TAG, "panel_link: event stream silent for %d s; reopening",
                         static_cast<int>(CONFIG_RELAY_CAPTION_SILENCE_S));
                break;
            }
            char now_url[sizeof(relay_settings_t::relay_addr)];
            if (!relay_address(now_url, sizeof now_url) || strcmp(now_url, url) != 0) {
                break;  // Relay moved, or the network went: start again with the address as it is now
            }
            continue;
        }
        break;
    }
    const int status = stream->http.status();
    *open_ms = stream->opened ? static_cast<uint32_t>((esp_timer_get_time() - stream->opened_at) / 1000) : 0;
    s_.stream_open = false;
    delete stream;
    shutdown(fd, SHUT_RDWR);
    close(fd);
    return status;
}

void events_task(void *)
{
    char *sse_buf = static_cast<char *>(heap_caps_malloc(kSseBytes, MALLOC_CAP_8BIT));
    if (sse_buf == nullptr) {
        ESP_LOGE(TAG, "panel_link: no memory for the event stream");
        vTaskDelete(nullptr);
        return;
    }
    uint32_t failures = 0;
    for (;;) {
        char url[sizeof(relay_settings_t::relay_addr)];
        // The stream needs Relay to know this panel, which the check-in is what tells it.
        if (!s_.registered || !relay_address(url, sizeof url)) {
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }
        uint32_t open_ms = 0;
        const int status = run_stream(url, sse_buf, &open_ms);
        failures = caption_failures_after(failures, open_ms > 0, open_ms);
        if (status == 404) {
            // Relay does not know this panel (it restarted): check in again now, and wait for that.
            ESP_LOGI(TAG, "panel_link: Relay does not know this panel; checking in again");
            s_.registered = false;
            xTaskNotifyGive(s_.checkin);
        }
        const uint32_t wait = panel_events_wait_ms(failures, status);
        vTaskDelay(pdMS_TO_TICKS(wait + jitter_ms(wait / 4 + 1)));
    }
}

}  // namespace

esp_err_t panel_link_start(const relay_settings_t &settings)
{
    if (s_started) {
        return ESP_OK;
    }
    if (!settings_panel_id_valid(settings.panel_id)) {
        ESP_LOGW(TAG, "panel_link: no panel ID; not checking in");
        return ESP_OK;
    }
    snprintf(s_.id, sizeof s_.id, "%s", settings.panel_id);
    if (xTaskCreatePinnedToCore(checkin_task, "panel_link", 6144, nullptr, 2, &s_.checkin, 0) != pdPASS ||
        xTaskCreatePinnedToCore(events_task, "panel_events", 5120, nullptr, 2, nullptr, 0) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    s_started = true;
    ESP_LOGI(TAG, "panel_link: started");
    return ESP_OK;
}

void panel_link_report()
{
    ESP_LOGI(TAG, "panel_link: report: %s, %u check-ins, stream %s, rev %u, named \"%s\"",
             s_started ? "running" : "not started", static_cast<unsigned>(s_.checkins.load()),
             s_.stream_open ? "open" : "closed", static_cast<unsigned>(s_.applied_rev.load()), s_.name);
}
