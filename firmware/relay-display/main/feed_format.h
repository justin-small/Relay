#pragma once

// What a person at the panel can choose to show, and what the panel does about a choice, with no LVGL or
// ESP-IDF in it, so `test_native.cpp` checks it on a laptop. `feeds.cpp` keeps the choice and drives the
// caption client; `caption_view.cpp` draws the menu and the panes.
//
// The pieces:
//  * the **options**: Transcription plus the live translations, read from Relay's `targets` message, and only
//    those the panel can draw (the caption font is Latin, so a language whose `script` is not "latin" is never
//    offered);
//  * a **pick**: one pane's choice, as saved in NVS;
//  * the **resolved** feed: what a pane shows now, which is its pick unless the pick is a language that is not
//    live, and then it is the transcription until the language returns;
//  * the **panes**: where the one or two of them sit on the glass.

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <initializer_list>

#include "caption_format.h"
#include "caption_view_format.h"
#include "pp_format.h"
#include "service_format.h"
#include "settings_format.h"

constexpr size_t kFeedLangBytes = sizeof(relay_settings_t::feed_lang);
constexpr int kFeedOptionsMax = 24;
constexpr size_t kFeedLabelBytes = 32;

// ---------------------------------------------------------------------------
// A pick
// ---------------------------------------------------------------------------

// One pane's choice: kFeedNone, kFeedTranscription, kFeedTranslation with the language as Relay names it,
// kFeedService with the Planning Center service type id in `lang`, or kFeedAlert (the alert buttons, #121).
struct feed_pick {
    uint8_t stream = kFeedNone;
    char lang[kFeedLangBytes] = {};
};

inline feed_pick feed_pick_make(uint8_t stream, const char *lang)
{
    feed_pick p;
    if (stream > kFeedLast) {
        return p;
    }
    p.stream = stream;
    if ((stream == kFeedTranslation || stream == kFeedService) && lang != nullptr) {
        settings_set_text(p.lang, sizeof p.lang, lang);
    }
    return p;
}

inline bool feed_pick_same(const feed_pick &a, const feed_pick &b)
{
    return a.stream == b.stream && strcmp(a.lang, b.lang) == 0;
}

// ---------------------------------------------------------------------------
// The options
// ---------------------------------------------------------------------------

struct feed_option {
    feed_pick pick;
    char label[kFeedLabelBytes] = {};
};

struct feed_options {
    // False until a `targets` message has been read. Until then nothing can be said about what is live.
    bool known = false;
    int count = 0;
    feed_option items[kFeedOptionsMax];

    bool has(const feed_pick &p) const
    {
        for (int i = 0; i < count; ++i) {
            if (feed_pick_same(items[i].pick, p)) {
                return true;
            }
        }
        return false;
    }

    // The first translation that is not `not_this`, or null.
    const feed_option *first_translation_but(const feed_pick &not_this) const
    {
        for (int i = 0; i < count; ++i) {
            if (items[i].pick.stream == kFeedTranslation && !feed_pick_same(items[i].pick, not_this)) {
                return &items[i];
            }
        }
        return nullptr;
    }
};

// **Only a Latin script is offered.** The panel carries one small Latin font, so a language Relay marks
// "cyrillic", "han" and so on would draw as boxes. A missing `script` is not offered either: what the panel
// cannot be sure of it does not show.
inline bool feed_script_drawable(const char *script)
{
    return script != nullptr && strcmp(script, "latin") == 0;
}

// Read a `targets` message (`{"type":"targets","source":{..,"script":..},"live":[{"target":..,"label":..,
// "script":..},..],..}`) into the options: Transcription first when the source language can be drawn, then the
// live translations that can be, in Relay's order (it sorts them by label), at most `kFeedOptionsMax`.
// False when it is not JSON the client reads, and `*out` is then untouched.
inline bool feed_options_parse(const char *json, size_t len, feed_options *out)
{
    const char *e = json + len;
    const char *p = caption_json::ws(json, e);
    if (p == nullptr || p >= e || *p != '{' || caption_json::skip_value(p, e) == nullptr) {
        return false;
    }
    feed_options o;
    o.known = true;

    char script[24] = {};
    const char *source = caption_json::get(p, e, "source");
    if (source != nullptr) {
        const char *sv = caption_json::get(source, e, "script");
        if (sv != nullptr) {
            caption_json::read_string(sv, e, script, sizeof script, nullptr);
        }
    }
    if (feed_script_drawable(script)) {
        feed_option &t = o.items[o.count++];
        t.pick = feed_pick_make(kFeedTranscription, "");
        settings_set_text(t.label, sizeof t.label, "Transcription");
    }

    caption_json::array_iter it;
    const char *live = caption_json::get(p, e, "live");
    if (live != nullptr && caption_json::array_begin(live, e, &it)) {
        while (o.count < kFeedOptionsMax) {
            const char *item = caption_json::array_next(&it);
            if (item == nullptr) {
                break;
            }
            char target[kFeedLangBytes + 8] = {};
            char label[kFeedLabelBytes + 8] = {};
            char item_script[24] = {};
            size_t target_len = 0;
            const char *tv = caption_json::get(item, e, "target");
            if (tv == nullptr || !caption_json::read_string(tv, e, target, sizeof target, &target_len) ||
                target_len == 0 || target_len >= kFeedLangBytes) {
                continue;
            }
            const char *sv = caption_json::get(item, e, "script");
            if (sv != nullptr) {
                caption_json::read_string(sv, e, item_script, sizeof item_script, nullptr);
            }
            if (!feed_script_drawable(item_script)) {
                continue;
            }
            const char *lv = caption_json::get(item, e, "label");
            if (lv != nullptr) {
                caption_json::read_string(lv, e, label, sizeof label, nullptr);
            }
            feed_option &t = o.items[o.count++];
            t.pick = feed_pick_make(kFeedTranslation, target);
            settings_set_text(t.label, sizeof t.label, label[0] != '\0' ? label : target);
        }
    }
    *out = o;
    return true;
}

// The menu with the Planning Center service added: Relay's `service` offers, as the panel's service mode allows.
// Off offers none; a service type the operator set offers only that one. One offer reads "Service"; several read
// "Service: <name>". They come after the captions.
inline feed_options feed_options_with_service(const feed_options &base, const service_offers &offers,
                                              const panel_service &mode)
{
    feed_options out = base;
    if (mode.mode == service_mode::off) {
        return out;
    }
    int shown = 0;
    for (int i = 0; i < offers.count; ++i) {
        if (mode.type[0] == '\0' || strcmp(mode.type, offers.items[i].type) == 0) {
            ++shown;
        }
    }
    for (int i = 0; i < offers.count && out.count < kFeedOptionsMax; ++i) {
        const service_offer &o = offers.items[i];
        if (mode.type[0] != '\0' && strcmp(mode.type, o.type) != 0) {
            continue;
        }
        feed_option &t = out.items[out.count++];
        t.pick = feed_pick_make(kFeedService, o.type);
        char ascii[kFeedLabelBytes] = {};
        service_ascii(o.label, ascii, sizeof ascii);
        if (shown == 1) {
            settings_set_text(t.label, sizeof t.label, "Service");
        } else {
            char label[kFeedLabelBytes + 16];
            snprintf(label, sizeof label, "Service: %s", ascii);
            settings_set_text(t.label, sizeof t.label, label);
        }
    }
    return out;
}

// The menu with ProPresenter's lyrics and timers (#138), as Relay's `targets` offers them. Not offered, and a panel
// already on one keeps showing it: its pane says what is wrong (see feed_resolve).
//
// **A feed_options is about 1.5 KB, and the feeds task has 6 KB of stack**: each of these has an in-place form
// (`feed_options_add_*`) so the task's step can build the menu in a static, not in a chain of temporaries. Nesting
// the by-value forms overflowed that stack once the third was added (found by the emulator suite).
inline void feed_options_add_propresenter(feed_options *out, const pp_offers &offers)
{
    if (offers.lyrics && out->count < kFeedOptionsMax) {
        feed_option &t = out->items[out->count++];
        t.pick = feed_pick_make(kFeedLyrics, "");
        settings_set_text(t.label, sizeof t.label, "Lyrics");
    }
    if (offers.timers && out->count < kFeedOptionsMax) {
        feed_option &t = out->items[out->count++];
        t.pick = feed_pick_make(kFeedTimers, "");
        settings_set_text(t.label, sizeof t.label, "Timers");
    }
}

inline feed_options feed_options_with_propresenter(const feed_options &base, const pp_offers &offers)
{
    feed_options out = base;
    feed_options_add_propresenter(&out, offers);
    return out;
}

// The status view (#224), offered while Relay's `targets` says `"status": true` (an older Relay has no status feed).
inline bool status_offered_parse(const char *json, size_t len)
{
    const char *e = json + len;
    bool b = false;
    const char *v = caption_json::get(json, e, "status");
    return v != nullptr && caption_json::read_bool(v, e, &b) && b;
}

inline void feed_options_add_status(feed_options *out, bool offered)
{
    if (offered && out->count < kFeedOptionsMax) {
        feed_option &t = out->items[out->count++];
        t.pick = feed_pick_make(kFeedStatus, "");
        settings_set_text(t.label, sizeof t.label, "Status");
    }
}

// The menu with the alert buttons (#121) last. Always offered: the pane says itself when Relay has no buttons.
inline void feed_options_add_alert(feed_options *out)
{
    if (out->count < kFeedOptionsMax) {
        feed_option &t = out->items[out->count++];
        t.pick = feed_pick_make(kFeedAlert, "");
        settings_set_text(t.label, sizeof t.label, "Alert buttons");
    }
}

inline feed_options feed_options_with_alert(const feed_options &base)
{
    feed_options out = base;
    feed_options_add_alert(&out);
    return out;
}

// Whether two option lists offer the same things, so a poll that says nothing new changes nothing.
inline bool feed_options_same(const feed_options &a, const feed_options &b)
{
    if (a.known != b.known || a.count != b.count) {
        return false;
    }
    for (int i = 0; i < a.count; ++i) {
        if (!feed_pick_same(a.items[i].pick, b.items[i].pick) || strcmp(a.items[i].label, b.items[i].label) != 0) {
            return false;
        }
    }
    return true;
}

// ---------------------------------------------------------------------------
// What a pane shows
// ---------------------------------------------------------------------------

struct feed_resolved {
    feed_pick pick;     // what the pane's caption slot is pointed at
    bool fallback;      // the pane's own pick is a language that is not live, and this is the transcription
};

// **A language that stops being live falls back to the transcription, and the pick is kept**, so the pane
// switches back the moment the language is live again. Nothing is saved when it falls back.
//
//  * No pick is the transcription: a panel nobody has chosen on shows it.
//  * With `options.known`, a translation is shown only if it is on offer. Before the first `targets`,
//    nothing is known, and the pick is tried as it is; `refused` (Relay answered it with a 4xx, which it
//    does for a language it does not offer) is then what says it is not live.
//  * A transcription that is not on offer (a source language the panel cannot draw) shows nothing.
//  * A Service pick is always shown as it is: its pane says when there is no service.
inline feed_resolved feed_resolve(const feed_pick &saved, const feed_options &options, bool refused)
{
    const feed_pick transcription = feed_pick_make(kFeedTranscription, "");
    feed_pick want = saved.stream == kFeedNone ? transcription : saved;
    if (want.stream == kFeedTranscription) {
        if (options.known && !options.has(want)) {
            return {feed_pick(), false};
        }
        return {want, false};
    }
    if (want.stream == kFeedService || want.stream == kFeedAlert || want.stream == kFeedLyrics ||
        want.stream == kFeedTimers || want.stream == kFeedStatus) {
        // **A Service pick never falls back to the transcription.** Nor does lyrics or timers: each pane says what is wrong. The Service pane says itself when there is
        // nothing to show (no service planned today, Relay does not follow it, Planning Center unreachable), so it
        // is shown whether or not the menu offers it right now.
        return {want, false};
    }
    const bool live = options.known ? options.has(want) : !refused;
    if (live) {
        return {want, false};
    }
    if (options.known && !options.has(transcription)) {
        return {feed_pick(), true};
    }
    return {transcription, true};
}

// The second pane's pick when a person turns on two feeds and has not chosen for it: the first live
// translation that is not the first pane's (the `/both` page's pairing), else the transcription.
inline feed_pick feed_default_second(const feed_options &options, const feed_pick &first)
{
    const feed_option *t = options.first_translation_but(first);
    if (t != nullptr && first.stream != kFeedNone) {
        return t->pick;
    }
    if (first.stream == kFeedTranscription && t != nullptr) {
        return t->pick;
    }
    return feed_pick_make(kFeedTranscription, "");
}

// A language as the header words it: "Spanish" from "SPANISH", "Chinese Simplified" from
// "CHINESE_SIMPLIFIED". The same words `caption_feed_title` gives a translation.
inline size_t feed_language_words(const char *lang, char *out, size_t cap)
{
    return caption_feed_title(kFeedTranslation, lang, out, cap);
}

// The notice a pane shows while its pick is not live: "Spanish is not live". Empty when there is nothing to say.
inline size_t feed_fallback_note(const feed_pick &saved, char *out, size_t cap)
{
    if (cap == 0) {
        return 0;
    }
    out[0] = '\0';
    if (saved.stream == kFeedService) {
        const int n = snprintf(out, cap, "The service is not shown on this panel");
        return n > 0 ? static_cast<size_t>(n) : 0;
    }
    if (saved.stream != kFeedTranslation || saved.lang[0] == '\0') {
        return 0;
    }
    char words[kFeedLabelBytes] = {};
    feed_language_words(saved.lang, words, sizeof words);
    size_t o = 0;
    for (const char *s : {static_cast<const char *>(words), " is not live"}) {
        for (const char *c = s; *c != '\0' && o + 1 < cap; ++c) {
            out[o++] = *c;
        }
    }
    out[o] = '\0';
    return o;
}

// ---------------------------------------------------------------------------
// The panes
// ---------------------------------------------------------------------------

constexpr int kFeedPaneDividerPx = 4;

struct feed_pane_rect {
    int x;
    int y;
    int w;
    int h;
    bool stacked;  // two panes one above the other (an upright panel), not side by side
};

// Where pane `index` sits on a `panel_w` x `panel_h` picture: all of it for one feed. Two feeds are two halves with a
// divider between them, side by side on a landscape picture and one above the other on an upright one (90 or 270,
// #122), where side by side would leave each a column too narrow to read.
inline feed_pane_rect feed_pane_rect_for(bool split, int index, int panel_w, int panel_h)
{
    if (!split) {
        return {0, 0, panel_w, panel_h, false};
    }
    if (panel_h > panel_w) {
        const int half = (panel_h - kFeedPaneDividerPx) / 2;
        return index == 0 ? feed_pane_rect{0, 0, panel_w, half, true}
                          : feed_pane_rect{0, panel_h - half, panel_w, half, true};
    }
    const int half = (panel_w - kFeedPaneDividerPx) / 2;
    return index == 0 ? feed_pane_rect{0, 0, half, panel_h, false} : feed_pane_rect{panel_w - half, 0, half, panel_h, false};
}
