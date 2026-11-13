// The caption feeds' sockets and tasks. The rules are in `caption_format.h`; `captions.h` says what
// this file is for.

#include "captions.h"
#include "firmware_update.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <new>

#include "config.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"
#include "net.h"
#include "sdkconfig.h"
#include "settings_format.h"

namespace {

const char *TAG = "captions";

// **A snapshot is one event, held whole.** Relay's history can be 500 lines, and a snapshot of them is
// one `data:` line of tens of kilobytes, so each feed has a quarter megabyte of PSRAM for it. The
// emulator has no PSRAM, and its snapshots are the test's, so it gets a small one in internal RAM. An
// event larger than the buffer is dropped and logged; the lines after it still arrive.
constexpr size_t kSseBytesPsram = 256 * 1024;
constexpr size_t kSseBytesInternal = 32 * 1024;

// How long a connection may take to open. lwIP's own wait for a SYN that is never answered is minutes.
constexpr int kConnectTimeoutMs = 5000;

// Relay pings every 15 s of quiet (app/main.py HEARTBEAT_S). A stream with nothing at all for this long
// is a connection that died without a FIN, which a Wi-Fi drop leaves behind, and is opened again.
constexpr int64_t kSilenceUs = static_cast<int64_t>(CONFIG_RELAY_CAPTION_SILENCE_S) * 1000 * 1000;

// Lines and the feed itself go to PSRAM where there is any, and to internal RAM where there is none.
void *feed_alloc(size_t n)
{
    return heap_caps_malloc_prefer(n, 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT, MALLOC_CAP_DEFAULT);
}

void feed_free(void *p)
{
    heap_caps_free(p);
}

struct Slot {
    int index = 0;
    SemaphoreHandle_t lock = nullptr;
    TaskHandle_t task = nullptr;

    // What was asked for, under `lock`. `wanted_gen` counts the changes.
    char url[sizeof(relay_settings_t::relay_addr)] = {};
    uint8_t stream = kFeedNone;
    char lang[sizeof(relay_settings_t::feed_lang)] = {};
    uint32_t wanted_gen = 0;

    // What readers see, under `lock`. A slot on the Service feed holds `service` instead of `feed`, and one on the
    // Alert feed (#121) `alerts`.
    caption_feed *feed = nullptr;
    service_state *service = nullptr;
    alert_board *alerts = nullptr;
    lyrics_state *lyrics = nullptr;   // ProPresenter's lyrics (#138)
    timers_state *timers = nullptr;   // and its timers
    status_state *status = nullptr;   // the status view (#224)
    caption_state state = caption_state::off;
    uint32_t revision = 0;

    // The task's own.
    char *sse = nullptr;
    size_t sse_cap = 0;
};

Slot s_slots[kCaptionSlots];
bool s_started = false;
// This panel's id, which the timers feed names it by (`captions_set_panel_id`). Written once at boot, before a feed
// can be pointed at the timers.
char s_panel_id[33] = {};
void (*volatile s_listener)(int, void *) = nullptr;
void *volatile s_listener_ctx = nullptr;

void lock(Slot &s)
{
    xSemaphoreTake(s.lock, portMAX_DELAY);
}

void unlock(Slot &s)
{
    xSemaphoreGive(s.lock);
}

void notify(Slot &s)
{
    auto fn = s_listener;
    if (fn != nullptr) {
        fn(s.index, s_listener_ctx);
    }
}

uint32_t wanted_gen(Slot &s)
{
    lock(s);
    const uint32_t gen = s.wanted_gen;
    unlock(s);
    return gen;
}

void set_state(Slot &s, caption_state state)
{
    lock(s);
    const bool changed = s.state != state;
    if (changed) {
        s.state = state;
        ++s.revision;
    }
    unlock(s);
    if (changed) {
        ESP_LOGI(TAG, "captions[%d]: %s", s.index, caption_state_name(state));
        notify(s);
    }
}

// A new, empty feed (or service state, for the Service feed), or none. The old one is freed.
bool replace_feed(Slot &s, bool make, uint8_t stream)
{
    caption_feed *fresh = nullptr;
    service_state *fresh_service = nullptr;
    alert_board *fresh_alerts = nullptr;
    lyrics_state *fresh_lyrics = nullptr;
    timers_state *fresh_timers = nullptr;
    status_state *fresh_status = nullptr;
    if (make && stream == kFeedStatus) {
        void *mem = feed_alloc(sizeof(status_state));
        if (mem == nullptr) {
            return false;
        }
        fresh_status = new (mem) status_state();
    } else if (make && stream == kFeedLyrics) {
        void *mem = feed_alloc(sizeof(lyrics_state));
        if (mem == nullptr) {
            return false;
        }
        fresh_lyrics = new (mem) lyrics_state();
    } else if (make && stream == kFeedTimers) {
        void *mem = feed_alloc(sizeof(timers_state));
        if (mem == nullptr) {
            return false;
        }
        fresh_timers = new (mem) timers_state();
    } else if (make && stream == kFeedService) {
        void *mem = feed_alloc(sizeof(service_state));
        if (mem == nullptr) {
            return false;
        }
        fresh_service = new (mem) service_state();
    } else if (make && stream == kFeedAlert) {
        void *mem = feed_alloc(sizeof(alert_board));
        if (mem == nullptr) {
            return false;
        }
        fresh_alerts = new (mem) alert_board();
    } else if (make) {
        void *mem = feed_alloc(sizeof(caption_feed));
        if (mem == nullptr) {
            return false;
        }
        fresh = new (mem) caption_feed(feed_alloc, feed_free);
    }
    lock(s);
    caption_feed *old = s.feed;
    service_state *old_service = s.service;
    alert_board *old_alerts = s.alerts;
    lyrics_state *old_lyrics = s.lyrics;
    timers_state *old_timers = s.timers;
    status_state *old_status = s.status;
    s.lyrics = fresh_lyrics;
    s.timers = fresh_timers;
    s.status = fresh_status;
    s.feed = fresh;
    s.service = fresh_service;
    s.alerts = fresh_alerts;
    ++s.revision;
    unlock(s);
    if (old_lyrics != nullptr) {
        old_lyrics->~lyrics_state();
        feed_free(old_lyrics);
    }
    if (old_timers != nullptr) {
        old_timers->~timers_state();
        feed_free(old_timers);
    }
    if (old_status != nullptr) {
        old_status->~status_state();
        feed_free(old_status);
    }
    if (old_alerts != nullptr) {
        old_alerts->~alert_board();
        feed_free(old_alerts);
    }
    if (old != nullptr) {
        old->~caption_feed();
        feed_free(old);
    }
    if (old_service != nullptr) {
        old_service->~service_state();
        feed_free(old_service);
    }
    return true;
}

// One event of the Service feed (`service_format.h`).
void on_service_event(Slot &s, const caption_sse_event &event)
{
    lock(s);
    service_change change = service_change::ignored;
    int items = 0;
    if (s.service != nullptr) {
        change = service_apply(s.service, event.data, event.len,
                               static_cast<uint32_t>(esp_timer_get_time() / 1000));
        items = s.service->count;
        if (change == service_change::state || change == service_change::tick) {
            ++s.revision;
        }
    }
    unlock(s);
    if (change == service_change::state) {
        ESP_LOGI(TAG, "captions[%d]: service, %d items", s.index, items);
        notify(s);
    } else if (change == service_change::tick) {
        notify(s);
    } else if (change == service_change::malformed) {
        ESP_LOGW(TAG, "captions[%d]: unreadable service message (%u bytes)", s.index,
                 static_cast<unsigned>(event.len));
    }
}

// One event of the Alert feed (`alert_format.h`): every button, whole.
void on_alert_event(Slot &s, const caption_sse_event &event)
{
    lock(s);
    alert_change change = alert_change::ignored;
    int count = 0;
    if (s.alerts != nullptr) {
        change = alert_apply(s.alerts, event.data, event.len, static_cast<uint32_t>(esp_timer_get_time() / 1000));
        count = s.alerts->count;
        if (change == alert_change::state || change == alert_change::tick) {
            ++s.revision;
        }
    }
    unlock(s);
    if (change == alert_change::state) {
        ESP_LOGI(TAG, "captions[%d]: alerts, %d buttons", s.index, count);
        notify(s);
    } else if (change == alert_change::tick) {
        notify(s);
    } else if (change == alert_change::malformed) {
        ESP_LOGW(TAG, "captions[%d]: unreadable alerts message (%u bytes)", s.index, static_cast<unsigned>(event.len));
    }
}

// One event of the lyrics feed (`pp_format.h`): the whole slide. A repeat that says what the glass already says
// changes nothing, so a still screen is not told to redraw (`lyrics_change::same`).
void on_lyrics_event(Slot &s, const caption_sse_event &event)
{
    lock(s);
    lyrics_change change = lyrics_change::ignored;
    if (s.lyrics != nullptr) {
        change = lyrics_apply(s.lyrics, event.data, event.len);
        if (change == lyrics_change::state) {
            ++s.revision;
        }
    }
    unlock(s);
    if (change == lyrics_change::state) {
        ESP_LOGI(TAG, "captions[%d]: lyrics changed", s.index);
        notify(s);
    } else if (change == lyrics_change::malformed) {
        ESP_LOGW(TAG, "captions[%d]: unreadable lyrics message (%u bytes)", s.index, static_cast<unsigned>(event.len));
    }
}

// One event of the timers feed (`pp_format.h`). Every message re-anchors the count, but only a change to the rows
// themselves is announced.
void on_timers_event(Slot &s, const caption_sse_event &event)
{
    lock(s);
    timers_change change = timers_change::ignored;
    int count = 0;
    if (s.timers != nullptr) {
        change = timers_apply(s.timers, event.data, event.len, static_cast<uint32_t>(esp_timer_get_time() / 1000));
        count = s.timers->count;
        if (change == timers_change::state) {
            ++s.revision;
        }
    }
    unlock(s);
    if (change == timers_change::state) {
        ESP_LOGI(TAG, "captions[%d]: timers changed, %d shown", s.index, count);
        notify(s);
    } else if (change == timers_change::malformed) {
        ESP_LOGW(TAG, "captions[%d]: unreadable timers message (%u bytes)", s.index, static_cast<unsigned>(event.len));
    }
}

void on_status_event(Slot &s, const caption_sse_event &event)
{
    lock(s);
    status_change change = status_change::ignored;
    if (s.status != nullptr) {
        change = status_apply(s.status, event.data, event.len, static_cast<uint32_t>(esp_timer_get_time() / 1000));
        if (change == status_change::state) {
            ++s.revision;
        }
    }
    unlock(s);
    if (change == status_change::state) {
        ESP_LOGI(TAG, "captions[%d]: status changed", s.index);
        notify(s);
    } else if (change == status_change::malformed) {
        ESP_LOGW(TAG, "captions[%d]: unreadable status message (%u bytes)", s.index, static_cast<unsigned>(event.len));
    }
}

// Wait up to `ms`, or until the feed is changed.
void rest(int ms)
{
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(ms));
}

bool has_address()
{
    return net_station_has_address() || net_wired_has_address();
}

// ---------------------------------------------------------------------------
// One connection
// ---------------------------------------------------------------------------

struct Outcome {
    caption_end end = caption_end::closed;
    int status = 0;
    bool opened = false;
    uint32_t open_ms = 0;
};

struct Conn {
    Slot *slot;
    caption_http http;
    caption_sse sse;
    bool opened = false;
    bool rejected = false;
    int64_t opened_at = 0;
    uint64_t resumed_from = 0;
    uint8_t stream = kFeedNone;   // what this connection was opened for

    Conn(Slot *s) : slot(s), sse(s->sse, s->sse_cap) {}
};

void on_event(void *ctx, const caption_sse_event &event)
{
    auto *c = static_cast<Conn *>(ctx);
    Slot &s = *c->slot;
    if (event.oversize) {
        ESP_LOGW(TAG, "captions[%d]: an event larger than %u bytes was dropped", s.index,
                 static_cast<unsigned>(s.sse_cap));
        return;
    }
    if (c->stream == kFeedService) {
        on_service_event(s, event);
        return;
    }
    if (c->stream == kFeedAlert) {
        on_alert_event(s, event);
        return;
    }
    if (c->stream == kFeedLyrics) {
        on_lyrics_event(s, event);
        return;
    }
    if (c->stream == kFeedTimers) {
        on_timers_event(s, event);
        return;
    }
    if (c->stream == kFeedStatus) {
        on_status_event(s, event);
        return;
    }
    lock(s);
    caption_change change = caption_change::ignored;
    int kept = 0;
    uint64_t resume = 0;
    uint64_t newest = 0;
    if (s.feed != nullptr) {
        change = s.feed->apply(event.data, event.len);
        kept = s.feed->lines().size();
        resume = s.feed->resume_id();
        newest = kept > 0 ? s.feed->lines().at(kept - 1).seq : 0;
        if (change != caption_change::ignored && change != caption_change::malformed) {
            ++s.revision;
        }
    }
    unlock(s);

    switch (change) {
    case caption_change::line:
        firmware_update_note_line();  // the quiet moment an update waits for starts again
        ESP_LOGI(TAG, "captions[%d]: line %llu (%d kept)", s.index, static_cast<unsigned long long>(newest), kept);
        break;
    case caption_change::rebuilt:
        ESP_LOGI(TAG, "captions[%d]: snapshot, %d lines, resume after %llu", s.index, kept,
                 static_cast<unsigned long long>(resume));
        break;
    case caption_change::cleared:
        ESP_LOGI(TAG, "captions[%d]: reset, buffer cleared", s.index);
        break;
    case caption_change::targets:
        ESP_LOGI(TAG, "captions[%d]: targets (%u bytes)", s.index, static_cast<unsigned>(event.len));
        break;
    case caption_change::no_memory:
        ESP_LOGE(TAG, "captions[%d]: no memory for a line; it is not shown", s.index);
        break;
    case caption_change::malformed:
        ESP_LOGW(TAG, "captions[%d]: unreadable message (%u bytes)", s.index, static_cast<unsigned>(event.len));
        return;
    case caption_change::ignored:
        return;
    }
    notify(s);
}

void on_comment(void *, const char *)
{
    // `: ping`. Any byte resets the silence clock, so there is nothing more to do.
}

// The response's headers are in: is this a caption stream?
void check_open(Conn &c)
{
    if (c.opened || c.rejected || c.http.state() != caption_http::phase::body) {
        return;
    }
    if (c.http.status() != 200 || !c.http.event_stream()) {
        c.rejected = true;
        return;
    }
    c.opened = true;
    c.opened_at = esp_timer_get_time();
    if (c.resumed_from != 0) {
        ESP_LOGI(TAG, "captions[%d]: stream open, resuming after %llu", c.slot->index,
                 static_cast<unsigned long long>(c.resumed_from));
    } else {
        ESP_LOGI(TAG, "captions[%d]: stream open, fresh", c.slot->index);
    }
    set_state(*c.slot, caption_state::connected);
}

void on_body(void *ctx, const char *p, size_t n)
{
    auto *c = static_cast<Conn *>(ctx);
    check_open(*c);
    if (c->opened) {
        c->sse.feed(p, n, on_event, on_comment, c);
    }
}

int open_socket(const char *host, uint16_t port, caption_end *why)
{
    addrinfo hints = {};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    char port_text[6];
    snprintf(port_text, sizeof port_text, "%u", static_cast<unsigned>(port));
    addrinfo *found = nullptr;
    if (getaddrinfo(host, port_text, &hints, &found) != 0 || found == nullptr) {
        *why = caption_end::unreachable;
        return -1;
    }
    const int fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (fd < 0) {
        freeaddrinfo(found);
        *why = caption_end::unreachable;
        return -1;
    }
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL, 0) | O_NONBLOCK);
    int rc = connect(fd, found->ai_addr, found->ai_addrlen);
    freeaddrinfo(found);
    int err = rc == 0 ? 0 : errno;
    if (rc != 0 && err == EINPROGRESS) {
        fd_set writable;
        FD_ZERO(&writable);
        FD_SET(fd, &writable);
        timeval tv = {kConnectTimeoutMs / 1000, (kConnectTimeoutMs % 1000) * 1000};
        rc = select(fd + 1, nullptr, &writable, nullptr, &tv);
        if (rc <= 0) {
            err = ETIMEDOUT;
        } else {
            socklen_t len = sizeof err;
            getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len);
        }
    }
    if (err != 0) {
        close(fd);
        // lwIP reports a refused SYN as a reset.
        *why = (err == ECONNREFUSED || err == ECONNRESET) ? caption_end::refused : caption_end::unreachable;
        return -1;
    }
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL, 0) & ~O_NONBLOCK);
    timeval rx = {1, 0};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &rx, sizeof rx);
    timeval tx = {5, 0};
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tx, sizeof tx);
    return fd;
}

bool send_all(int fd, const char *p, size_t n)
{
    while (n > 0) {
        const ssize_t sent = send(fd, p, n, 0);
        if (sent <= 0) {
            return false;
        }
        p += sent;
        n -= static_cast<size_t>(sent);
    }
    return true;
}

Outcome run_connection(Slot &s, uint32_t gen, const char *url, uint8_t stream, const char *lang)
{
    Outcome out;
    char host[sizeof(relay_settings_t::relay_addr)];
    uint16_t port = 0;
    if (!caption_url_split(url, host, sizeof host, &port)) {
        out.end = caption_end::unreachable;
        return out;
    }
    lock(s);
    const uint64_t resume = s.feed != nullptr ? s.feed->resume_id() : 0;
    unlock(s);

    char request[384];
    // The timers feed names this panel; every other feed's `lang` is what the saved choice holds.
    const size_t request_len = caption_request(request, sizeof request, host, port, stream,
                                               stream == kFeedTimers ? s_panel_id : lang, resume, RELAY_FW_VERSION);
    if (request_len == 0) {
        out.end = caption_end::bad_response;
        return out;
    }
    const int fd = open_socket(host, port, &out.end);
    if (fd < 0) {
        return out;
    }
    if (!send_all(fd, request, request_len)) {
        close(fd);
        out.end = caption_end::closed;
        return out;
    }

    auto *conn = new (std::nothrow) Conn(&s);
    if (conn == nullptr) {
        close(fd);
        out.end = caption_end::closed;
        return out;
    }
    conn->resumed_from = resume;
    conn->stream = stream;
    char buf[1024];
    int64_t last_rx = esp_timer_get_time();
    for (;;) {
        if (wanted_gen(s) != gen) {
            out.end = caption_end::stopped;
            break;
        }
        const ssize_t n = recv(fd, buf, sizeof buf, 0);
        const int64_t now = esp_timer_get_time();
        if (n > 0) {
            last_rx = now;
            conn->http.feed(buf, static_cast<size_t>(n), on_body, conn);
            check_open(*conn);
            if (conn->rejected || conn->http.state() == caption_http::phase::bad) {
                out.end = conn->http.status() != 200 ? caption_end::http_status : caption_end::bad_response;
                break;
            }
            if (conn->http.state() == caption_http::phase::done) {
                out.end = caption_end::closed;
                break;
            }
            continue;
        }
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            if (now - last_rx > kSilenceUs) {
                ESP_LOGW(TAG, "captions[%d]: silent for %d s; reconnecting", s.index,
                         static_cast<int>(CONFIG_RELAY_CAPTION_SILENCE_S));
                out.end = caption_end::silent;
                break;
            }
            continue;
        }
        out.end = caption_end::closed;
        break;
    }
    out.status = conn->http.status();
    out.opened = conn->opened;
    if (conn->opened) {
        out.open_ms = static_cast<uint32_t>((esp_timer_get_time() - conn->opened_at) / 1000);
    }
    delete conn;
    shutdown(fd, SHUT_RDWR);
    close(fd);
    return out;
}

struct TargetsBody {
    char *out;
    size_t cap;
    size_t len;
    bool overflow;
};

void on_targets_body(void *ctx, const char *p, size_t n)
{
    auto *b = static_cast<TargetsBody *>(ctx);
    if (b->len + n + 1 > b->cap) {
        b->overflow = true;
        return;
    }
    memcpy(b->out + b->len, p, n);
    b->len += n;
    b->out[b->len] = '\0';
}

const char *end_name(caption_end end)
{
    switch (end) {
    case caption_end::closed: return "closed by the server";
    case caption_end::silent: return "silent";
    case caption_end::refused: return "refused";
    case caption_end::unreachable: return "unreachable";
    case caption_end::http_status: return "answered";
    case caption_end::bad_response: return "not a caption stream";
    case caption_end::stopped: return "stopped";
    }
    return "?";
}

// ---------------------------------------------------------------------------
// The slot's task
// ---------------------------------------------------------------------------

void slot_task(void *arg)
{
    Slot &s = *static_cast<Slot *>(arg);
    uint32_t feed_gen = 0;
    uint32_t failures = 0;
    bool tried = false;
    for (;;) {
        lock(s);
        const uint32_t gen = s.wanted_gen;
        char url[sizeof s.url];
        char lang[sizeof s.lang];
        memcpy(url, s.url, sizeof url);
        memcpy(lang, s.lang, sizeof lang);
        const uint8_t stream = s.stream;
        unlock(s);

        const bool wanted = stream != kFeedNone && url[0] != '\0';
        if (gen != feed_gen) {
            // A different feed: start over, with an empty buffer and nothing to resume.
            feed_gen = gen;
            failures = 0;
            tried = false;
            if (!replace_feed(s, wanted, stream)) {
                ESP_LOGE(TAG, "captions[%d]: no memory for the feed", s.index);
                set_state(s, caption_state::off);
                rest(5000);
                feed_gen = 0;
                continue;
            }
            if (wanted) {
                ESP_LOGI(TAG, "captions[%d]: feed %s %s from %s", s.index,
                         stream == kFeedTranscription ? "transcription"
                         : stream == kFeedService     ? "service"
                         : stream == kFeedAlert       ? "alert"
                         : stream == kFeedLyrics      ? "lyrics"
                         : stream == kFeedTimers      ? "timers"
                         : stream == kFeedStatus      ? "status"
                                                      : "translation",
                         stream == kFeedTranscription ? "" : lang, url);
            }
        }
        if (!wanted) {
            set_state(s, caption_state::off);
            ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
            continue;
        }
        if (s.sse == nullptr) {
            s.sse_cap = kSseBytesPsram;
            s.sse = static_cast<char *>(heap_caps_malloc(s.sse_cap, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
            if (s.sse == nullptr) {
                s.sse_cap = kSseBytesInternal;
                s.sse = static_cast<char *>(heap_caps_malloc(s.sse_cap, MALLOC_CAP_DEFAULT));
            }
            if (s.sse == nullptr) {
                ESP_LOGE(TAG, "captions[%d]: no memory for the event buffer", s.index);
                rest(5000);
                continue;
            }
            ESP_LOGI(TAG, "captions[%d]: event buffer %u bytes", s.index, static_cast<unsigned>(s.sse_cap));
        }
        if (!has_address()) {
            set_state(s, caption_state::waiting_for_network);
            rest(1000);
            continue;
        }
        // The state shown while a try is made is the last one's outcome, so "Relay not running" stays on
        // the screen through the retries instead of flickering to "connecting" each time.
        if (!tried) {
            set_state(s, caption_state::connecting);
        }
        tried = true;
        const Outcome got = run_connection(s, gen, url, stream, lang);
        if (got.end == caption_end::stopped) {
            continue;
        }
        failures = caption_failures_after(failures, got.opened, got.open_ms);
        const caption_state next = caption_state_after(got.end, got.status);
        uint32_t wait = caption_backoff_ms(failures == 0 ? 0 : failures - 1);
        wait += esp_random() % (wait / 4 + 1);
        if (got.end == caption_end::http_status) {
            ESP_LOGW(TAG, "captions[%d]: Relay answered %d; trying again in %u ms", s.index, got.status,
                     static_cast<unsigned>(wait));
        } else {
            ESP_LOGW(TAG, "captions[%d]: connection %s; trying again in %u ms", s.index, end_name(got.end),
                     static_cast<unsigned>(wait));
        }
        set_state(s, next);
        rest(static_cast<int>(wait));
    }
}

}  // namespace

esp_err_t captions_start()
{
    if (s_started) {
        return ESP_OK;
    }
    for (int i = 0; i < kCaptionSlots; ++i) {
        Slot &s = s_slots[i];
        s.index = i;
        s.lock = xSemaphoreCreateMutex();
        if (s.lock == nullptr) {
            return ESP_ERR_NO_MEM;
        }
        char name[16];
        snprintf(name, sizeof name, "captions%d", i);
        // Core 0, with the network: core 1 is the panel's (main.cpp).
        if (xTaskCreatePinnedToCore(slot_task, name, 6144, &s, 4, &s.task, 0) != pdPASS) {
            return ESP_ERR_NO_MEM;
        }
    }
    s_started = true;
    return ESP_OK;
}

esp_err_t captions_set_feed(int slot, const char *relay_url, uint8_t stream, const char *lang)
{
    if (!s_started || slot < 0 || slot >= kCaptionSlots || stream > kFeedLast) {
        return ESP_ERR_INVALID_ARG;
    }
    if (relay_url == nullptr) {
        relay_url = "";
    }
    if (lang == nullptr || (stream != kFeedTranslation && stream != kFeedService)) {
        lang = "";
    }
    Slot &s = s_slots[slot];
    lock(s);
    const bool same = s.stream == stream && strcmp(s.url, relay_url) == 0 && strcmp(s.lang, lang) == 0;
    if (!same) {
        settings_set_text(s.url, sizeof s.url, relay_url);
        settings_set_text(s.lang, sizeof s.lang, lang);
        s.stream = stream;
        ++s.wanted_gen;
    }
    unlock(s);
    if (!same) {
        xTaskNotifyGive(s.task);
    }
    return ESP_OK;
}

caption_state captions_state(int slot)
{
    if (!s_started || slot < 0 || slot >= kCaptionSlots) {
        return caption_state::off;
    }
    Slot &s = s_slots[slot];
    lock(s);
    const caption_state state = s.state;
    unlock(s);
    return state;
}

uint8_t captions_feed_choice(int slot, char *lang, size_t lang_cap)
{
    if (lang != nullptr && lang_cap > 0) {
        lang[0] = '\0';
    }
    if (!s_started || slot < 0 || slot >= kCaptionSlots) {
        return kFeedNone;
    }
    Slot &s = s_slots[slot];
    lock(s);
    const uint8_t stream = s.stream;
    if (lang != nullptr && lang_cap > 0) {
        snprintf(lang, lang_cap, "%s", s.lang);
    }
    unlock(s);
    return stream;
}

uint32_t captions_revision(int slot)
{
    if (!s_started || slot < 0 || slot >= kCaptionSlots) {
        return 0;
    }
    Slot &s = s_slots[slot];
    lock(s);
    const uint32_t rev = s.revision;
    unlock(s);
    return rev;
}

void captions_read(int slot, void (*fn)(const caption_feed *, caption_state, void *), void *ctx)
{
    if (!s_started || slot < 0 || slot >= kCaptionSlots) {
        fn(nullptr, caption_state::off, ctx);
        return;
    }
    Slot &s = s_slots[slot];
    lock(s);
    fn(s.feed, s.state, ctx);
    unlock(s);
}

void captions_read_alerts(int slot, void (*fn)(const alert_board *alerts, caption_state state, void *ctx), void *ctx)
{
    if (!s_started || slot < 0 || slot >= kCaptionSlots) {
        fn(nullptr, caption_state::off, ctx);
        return;
    }
    Slot &s = s_slots[slot];
    lock(s);
    fn(s.alerts, s.state, ctx);
    unlock(s);
}

void captions_read_lyrics(int slot, void (*fn)(const lyrics_state *lyrics, caption_state state, void *ctx), void *ctx)
{
    if (!s_started || slot < 0 || slot >= kCaptionSlots) {
        fn(nullptr, caption_state::off, ctx);
        return;
    }
    Slot &s = s_slots[slot];
    lock(s);
    fn(s.lyrics, s.state, ctx);
    unlock(s);
}

void captions_read_timers(int slot, void (*fn)(const timers_state *timers, caption_state state, void *ctx), void *ctx)
{
    if (!s_started || slot < 0 || slot >= kCaptionSlots) {
        fn(nullptr, caption_state::off, ctx);
        return;
    }
    Slot &s = s_slots[slot];
    lock(s);
    fn(s.timers, s.state, ctx);
    unlock(s);
}

void captions_read_status(int slot, void (*fn)(const status_state *status, caption_state state, void *ctx), void *ctx)
{
    if (!s_started || slot < 0 || slot >= kCaptionSlots) {
        fn(nullptr, caption_state::off, ctx);
        return;
    }
    Slot &s = s_slots[slot];
    lock(s);
    fn(s.status, s.state, ctx);
    unlock(s);
}

void captions_set_panel_id(const char *id)
{
    if (id != nullptr && settings_panel_id_valid(id)) {
        snprintf(s_panel_id, sizeof s_panel_id, "%s", id);
    }
}

void captions_read_service(int slot, void (*fn)(const service_state *service, caption_state state, void *ctx),
                           void *ctx)
{
    if (!s_started || slot < 0 || slot >= kCaptionSlots) {
        fn(nullptr, caption_state::off, ctx);
        return;
    }
    Slot &s = s_slots[slot];
    lock(s);
    fn(s.service, s.state, ctx);
    unlock(s);
}

void captions_set_listener(void (*fn)(int, void *), void *ctx)
{
    s_listener = nullptr;
    s_listener_ctx = ctx;
    s_listener = fn;
}

bool captions_fetch_targets(const char *relay_url, char *out, size_t cap)
{
    if (out == nullptr || cap < 2) {
        return false;
    }
    out[0] = '\0';
    if (relay_url == nullptr || !has_address()) {
        return false;
    }
    char host[sizeof(relay_settings_t::relay_addr)];
    uint16_t port = 0;
    if (!caption_url_split(relay_url, host, sizeof host, &port)) {
        return false;
    }
    char request[256];
    const size_t request_len = caption_targets_request(request, sizeof request, host, port, RELAY_FW_VERSION);
    caption_end why = caption_end::closed;
    const int fd = request_len > 0 ? open_socket(host, port, &why) : -1;
    if (fd < 0) {
        return false;
    }
    TargetsBody body = {out, cap, 0, false};
    caption_http *http = new (std::nothrow) caption_http();
    bool ok = false;
    if (http != nullptr && send_all(fd, request, request_len)) {
        char buf[512];
        const int64_t deadline = esp_timer_get_time() + 8 * 1000 * 1000;
        while (esp_timer_get_time() < deadline) {
            const ssize_t n = recv(fd, buf, sizeof buf, 0);
            if (n > 0) {
                if (!http->feed(buf, static_cast<size_t>(n), on_targets_body, &body)) {
                    break;
                }
                if (http->state() == caption_http::phase::done) {
                    break;
                }
                continue;
            }
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                continue;
            }
            break;  // closed: the body is whole (Connection: close)
        }
        ok = http->status() == 200 && !body.overflow && body.len > 0;
    }
    delete http;
    shutdown(fd, SHUT_RDWR);
    close(fd);
    if (!ok) {
        out[0] = '\0';
    }
    return ok;
}

void captions_dump(int slot)
{
    captions_read(
        slot,
        [](const caption_feed *feed, caption_state state, void *ctx) {
            const int index = *static_cast<int *>(ctx);
            const int n = feed != nullptr ? feed->lines().size() : 0;
            ESP_LOGI(TAG, "captions[%d]: dump begin, %s, %d lines, resume after %llu, %u lost", index,
                     caption_state_name(state), n,
                     static_cast<unsigned long long>(feed != nullptr ? feed->resume_id() : 0),
                     static_cast<unsigned>(feed != nullptr ? feed->lost() : 0));
            for (int i = 0; i < n; ++i) {
                const caption_line &line = feed->lines().at(i);
                ESP_LOGI(TAG, "captions[%d]: #%llu %s", index, static_cast<unsigned long long>(line.seq), line.text);
            }
            ESP_LOGI(TAG, "captions[%d]: dump end", index);
        },
        &slot);
}

bool captions_has_address()
{
    return has_address();
}

int captions_connect(const char *host, uint16_t port)
{
    caption_end why = caption_end::closed;
    return open_socket(host, port, &why);
}

bool captions_send(int fd, const char *p, size_t n)
{
    return send_all(fd, p, n);
}
