#include "feeds.h"

#include <cstdio>
#include <cstring>

#include "captions.h"
#include "esp_log.h"
#include "panel_link.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "screen.h"
#include "settings.h"

namespace {

const char *TAG = "feeds";

constexpr uint32_t kStepMs = 400;

struct State {
    SemaphoreHandle_t lock = nullptr;
    TaskHandle_t task = nullptr;
    feeds_view view;
    char url[sizeof(relay_settings_t::relay_addr)] = {};
    bool manual = false;
    bool dirty = false;
    // Relay answered a pane's language with a 4xx before it had said what is live. Cleared by the next `targets`.
    bool refused[2] = {false, false};
    uint32_t seen_targets_rev[2] = {0, 0};
    // What `GET /api/targets` last answered, from the poll task, under `lock`.
    char poll_json[kCaptionTargetsMax] = {};
    uint32_t poll_rev = 0;
    uint32_t seen_poll_rev = 0;
};

// The step's own, static for the task's stack: what Relay's `targets` offers (captions and service feeds apart), and
// the menu made from them with the panel's service mode.
feed_options s_base;
service_offers s_offers;
pp_offers s_pp_offers;   // ProPresenter's lyrics and timers, from the same `targets`
feed_options s_combined;
feed_options s_parsed;

State s_;
bool s_started = false;

// The `targets` message read from a slot, copied out under the slot's lock.
struct TargetsCopy {
    char json[kCaptionTargetsMax];
    uint32_t rev;
};
TargetsCopy s_copy;  // the step's only
char s_poll_buf[kCaptionTargetsMax];  // the poll task's only

void lock()
{
    xSemaphoreTake(s_.lock, portMAX_DELAY);
}

void unlock()
{
    xSemaphoreGive(s_.lock);
}

void describe(const feed_pick &p, char *out, size_t cap)
{
    if (p.stream == kFeedTranscription) {
        snprintf(out, cap, "transcription");
    } else if (p.stream == kFeedTranslation) {
        snprintf(out, cap, "translation %s", p.lang);
    } else if (p.stream == kFeedService) {
        snprintf(out, cap, "service %s", p.lang);
    } else if (p.stream == kFeedAlert) {
        snprintf(out, cap, "alert");
    } else if (p.stream == kFeedLyrics) {
        snprintf(out, cap, "lyrics");
    } else if (p.stream == kFeedTimers) {
        snprintf(out, cap, "timers");
    } else {
        snprintf(out, cap, "nothing");
    }
}

void read_targets(const caption_feed *feed, caption_state, void *)
{
    s_copy.rev = 0;
    s_copy.json[0] = '\0';
    if (feed != nullptr && feed->targets_revision() != 0) {
        snprintf(s_copy.json, sizeof s_copy.json, "%s", feed->targets());
        s_copy.rev = feed->targets_revision();
    }
}

// Read one `targets` message into the caption options and the service offers. Returns whether either changed.
bool take_targets(const char *json)
{
    if (!feed_options_parse(json, strlen(json), &s_parsed)) {
        return false;
    }
    const service_offers offers = service_offers_parse(json, strlen(json));
    bool changed = !feed_options_same(s_parsed, s_base);
    s_base = s_parsed;
    if (offers.count != s_offers.count || memcmp(&offers, &s_offers, sizeof offers) != 0) {
        s_offers = offers;
        changed = true;
    }
    const pp_offers pp = pp_offers_parse(json, strlen(json));
    if (pp.lyrics != s_pp_offers.lyrics || pp.timers != s_pp_offers.timers) {
        s_pp_offers = pp;
        changed = true;
    }
    return changed;
}

// The newest `targets` message either slot has read, if one is new since the last step. Returns whether the
// options changed.
bool update_options()
{
    bool changed = false;
    for (int slot = 0; slot < kCaptionSlots; ++slot) {
        captions_read(slot, read_targets, nullptr);
        if (s_copy.rev == 0 || s_copy.rev == s_.seen_targets_rev[slot]) {
            s_.seen_targets_rev[slot] = s_copy.rev;  // a rebuilt feed starts its count again
            continue;
        }
        s_.seen_targets_rev[slot] = s_copy.rev;
        changed = take_targets(s_copy.json) || changed;
    }
    // What Relay answered when asked (it does not say what is live when a stream opens, only when that changes).
    lock();
    const bool polled = s_.poll_rev != s_.seen_poll_rev;
    if (polled) {
        s_.seen_poll_rev = s_.poll_rev;
        memcpy(s_copy.json, s_.poll_json, sizeof s_copy.json);
    }
    unlock();
    if (polled) {
        changed = take_targets(s_copy.json) || changed;
    }
    return changed;
}

void persist()
{
    relay_settings_t out = {};
    lock();
    settings_defaults(&out);
    out.feed_stream = s_.view.saved[0].stream;
    settings_set_text(out.feed_lang, sizeof out.feed_lang, s_.view.saved[0].lang);
    out.feed2_stream = s_.view.saved[1].stream;
    settings_set_text(out.feed2_lang, sizeof out.feed2_lang, s_.view.saved[1].lang);
    out.feed_split = s_.view.split ? 1 : 0;
    s_.dirty = false;
    unlock();
    if (settings_save_feeds(&out) == ESP_OK) {
        ESP_LOGI(TAG, "feeds: saved");
    } else {
        ESP_LOGW(TAG, "feeds: could not save the choice");
    }
}

void step()
{
    const bool targets_changed = update_options();
    // The menu: what Relay offers, with the service feeds the panel's service mode allows. Made every step, since the
    // mode can change without a `targets` message.
    // In place, in the static: a feed_options is 1.5 KB and this task's stack is 6 KB (feed_format.h).
    s_combined = feed_options_with_service(s_base, s_offers, panel_link_service());
    feed_options_add_propresenter(&s_combined, s_pp_offers);
    feed_options_add_alert(&s_combined);
    const feed_options &options = s_combined;

    lock();
    const bool options_changed = !feed_options_same(options, s_.view.options);
    if (options_changed) {
        s_.view.options = options;
        ++s_.view.revision;
        ESP_LOGI(TAG, "feeds: %d on offer", options.count);
    }
    if (targets_changed) {
        s_.refused[0] = s_.refused[1] = false;
    }
    const bool manual = s_.manual;
    const bool split = s_.view.split;
    char url[sizeof s_.url];
    snprintf(url, sizeof url, "%s", s_.url);
    feed_pick saved[2] = {s_.view.saved[0], s_.view.saved[1]};
    bool refused[2] = {s_.refused[0], s_.refused[1]};
    unlock();

    if (!manual) {
        feed_resolved shown[2];
        for (int pane = 0; pane < 2; ++pane) {
            char lang[kFeedLangBytes];
            const uint8_t chosen = captions_feed_choice(pane, lang, sizeof lang);
            if (captions_state(pane) == caption_state::feed_refused && chosen == kFeedTranslation &&
                strcmp(lang, saved[pane].lang) == 0 && !refused[pane]) {
                refused[pane] = true;
                lock();
                s_.refused[pane] = true;
                unlock();
            }
            const bool active = pane == 0 || split;
            shown[pane] = active && url[0] != '\0' ? feed_resolve(saved[pane], options, refused[pane])
                                                  : feed_resolved{feed_pick(), false};
            captions_set_feed(pane, url, shown[pane].pick.stream, shown[pane].pick.lang);
        }
        lock();
        for (int pane = 0; pane < 2; ++pane) {
            const feed_resolved &old = s_.view.shown[pane];
            if (!feed_pick_same(old.pick, shown[pane].pick) || old.fallback != shown[pane].fallback) {
                char what[48];
                describe(shown[pane].pick, what, sizeof what);
                ESP_LOGI(TAG, "feeds: pane %d shows %s%s", pane + 1, what,
                         shown[pane].fallback ? " (its language is not live)" : "");
                s_.view.shown[pane] = shown[pane];
                ++s_.view.revision;
            }
        }
        unlock();
    }

    lock();
    const bool dirty = s_.dirty;
    unlock();
    if (dirty) {
        persist();
    }
}

void task(void *)
{
    for (;;) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(kStepMs));
        step();
    }
}

// Asks Relay what is live: quickly while that is not known, and now and then after, as a check on the stream's own
// `targets` messages. Its own task, because an unreachable Relay takes seconds to give up on.
void poll_task(void *)
{
    for (;;) {
        lock();
        char url[sizeof s_.url];
        snprintf(url, sizeof url, "%s", s_.url);
        const bool manual = s_.manual;
        const bool known = s_.view.options.known;
        unlock();
        if (!manual && url[0] != '\0' && captions_fetch_targets(url, s_poll_buf, sizeof s_poll_buf)) {
            lock();
            memcpy(s_.poll_json, s_poll_buf, sizeof s_.poll_json);
            ++s_.poll_rev;
            unlock();
        }
        vTaskDelay(pdMS_TO_TICKS(known ? 10000 : 2500));
    }
}

void wake()
{
    if (s_.task != nullptr) {
        xTaskNotifyGive(s_.task);
    }
}

}  // namespace

esp_err_t feeds_start(const relay_settings_t &settings)
{
    if (s_started) {
        return ESP_OK;
    }
    s_.lock = xSemaphoreCreateMutex();
    if (s_.lock == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    s_.view.saved[0] = feed_pick_make(settings.feed_stream, settings.feed_lang);
    s_.view.saved[1] = feed_pick_make(settings.feed2_stream, settings.feed2_lang);
    s_.view.split = settings.feed_split == 1;
    settings_set_text(s_.url, sizeof s_.url, settings.relay_addr);
    if (xTaskCreatePinnedToCore(task, "feeds", 6144, nullptr, 3, &s_.task, 0) != pdPASS ||
        xTaskCreatePinnedToCore(poll_task, "feeds_poll", 5120, nullptr, 2, nullptr, 0) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    s_started = true;
    if (s_.url[0] != '\0') {
        screen_show_captions();
    }
    ESP_LOGI(TAG, "feeds: started, %s", s_.view.split ? "two feeds" : "one feed");
    wake();
    return ESP_OK;
}

void feeds_snapshot(feeds_view *out)
{
    if (!s_started) {
        *out = feeds_view();
        return;
    }
    lock();
    *out = s_.view;
    unlock();
}

uint32_t feeds_revision()
{
    if (!s_started) {
        return 0;
    }
    lock();
    const uint32_t rev = s_.view.revision;
    unlock();
    return rev;
}

void feeds_choose(int pane, const feed_pick &pick)
{
    if (!s_started || pane < 0 || pane > 1) {
        return;
    }
    lock();
    s_.view.saved[pane] = pick;
    s_.refused[pane] = false;
    s_.dirty = true;
    ++s_.view.revision;
    unlock();
    char what[48];
    describe(pick, what, sizeof what);
    ESP_LOGI(TAG, "feeds: pane %d chosen: %s", pane + 1, what);
    wake();
}

void feeds_set_split(bool split)
{
    if (!s_started) {
        return;
    }
    lock();
    if (s_.view.split == split) {
        unlock();
        return;
    }
    s_.view.split = split;
    if (split && s_.view.saved[1].stream == kFeedNone) {
        const feed_pick first = s_.view.shown[0].pick.stream != kFeedNone ? s_.view.shown[0].pick : s_.view.saved[0];
        s_.view.saved[1] = feed_default_second(s_.view.options, first);
    }
    s_.dirty = true;
    ++s_.view.revision;
    unlock();
    ESP_LOGI(TAG, "feeds: %s", split ? "two feeds" : "one feed");
    wake();
}

bool feeds_relay_url(char *out, size_t cap)
{
    if (!s_started || cap == 0) {
        return false;
    }
    lock();
    snprintf(out, cap, "%s", s_.url);
    unlock();
    return out[0] != '\0';
}

void feeds_apply(const feed_pick *picks, int count)
{
    if (!s_started || picks == nullptr || count < 1 || count > 2) {
        return;
    }
    lock();
    s_.view.saved[0] = picks[0];
    s_.refused[0] = false;
    if (count == 2) {
        s_.view.saved[1] = picks[1];
        s_.refused[1] = false;
    }
    s_.view.split = count == 2;
    s_.dirty = true;
    ++s_.view.revision;
    unlock();
    ESP_LOGI(TAG, "feeds: set from Relay, %s", count == 2 ? "two feeds" : "one feed");
    wake();
}

void feeds_use_relay(const char *url)
{
    if (!s_started) {
        return;
    }
    lock();
    settings_set_text(s_.url, sizeof s_.url, url != nullptr ? url : "");
    s_.manual = false;
    ++s_.view.revision;
    unlock();
    screen_show_captions();
    wake();
}

void feeds_go_manual()
{
    if (!s_started) {
        return;
    }
    lock();
    s_.manual = true;
    unlock();
}

void feeds_report()
{
    // Static: the console task's stack is small, and a snapshot with its menu is a couple of kilobytes. The console
    // runs one command at a time.
    static feeds_view v;
    feeds_snapshot(&v);
    char saved1[48], saved2[48], shown1[48], shown2[48];
    describe(v.saved[0], saved1, sizeof saved1);
    describe(v.saved[1], saved2, sizeof saved2);
    describe(v.shown[0].pick, shown1, sizeof shown1);
    describe(v.shown[1].pick, shown2, sizeof shown2);
    char list[240] = "";
    size_t o = 0;
    for (int i = 0; i < v.options.count && o + 40 < sizeof list; ++i) {
        const feed_pick &pick = v.options.items[i].pick;
        o += snprintf(list + o, sizeof list - o, "%s%s%s", i > 0 ? "," : "", pick.stream == kFeedService ? "service:" : "",
                      pick.stream == kFeedTranscription ? "transcription"
                      : pick.stream == kFeedAlert       ? "alert"
                      : pick.stream == kFeedLyrics      ? "lyrics"
                      : pick.stream == kFeedTimers      ? "timers"
                                                        : pick.lang);
    }
    ESP_LOGI(TAG, "feeds: report: %s; pane 1 saved %s shows %s%s; pane 2 saved %s shows %s%s; options %s [%s]",
             v.split ? "split" : "single", saved1, shown1, v.shown[0].fallback ? " (fallback)" : "", saved2, shown2,
             v.shown[1].fallback ? " (fallback)" : "", v.options.known ? "known" : "unknown", list);
}

void feeds_service_changed()
{
    wake();
}
