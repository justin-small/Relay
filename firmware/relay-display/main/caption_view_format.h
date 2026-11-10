#pragma once

// The caption view's rules, with no LVGL or ESP-IDF in them, so `test_native.cpp` checks them on a laptop:
// when the list follows live and when it lets go, which lines on the glass need adding or removing when
// the buffer changes, and the words and colours of the header. `caption_view.cpp` is the drawing.

#include <cstddef>
#include <cstdint>
#include <cstring>

#include "caption_format.h"
#include "settings_format.h"

// ---------------------------------------------------------------------------
// Layout, in panel pixels (800 x 480)
// ---------------------------------------------------------------------------

constexpr int kCaptionHeaderH = 56;
constexpr int kCaptionPadX = 28;
constexpr int kCaptionPadY = 14;
// The gap between two lines. A line is 41 px tall in the caption font, so a screen holds about seven.
constexpr int kCaptionLineGap = 16;
constexpr int kCaptionJumpW = 280;
constexpr int kCaptionJumpH = 64;
constexpr int kCaptionJumpMargin = 20;

// A view this near the bottom, in px, counts as at the bottom. Touch scrolling does not land on the last pixel.
constexpr int kCaptionBottomSlackPx = 6;

// **A paused view returns to live by itself after this long with no finger on it.** A panel on a wall
// that someone scrolled back and walked away from would otherwise show old lines for good.
constexpr uint32_t kCaptionAutoLiveMs = 60000;

// ---------------------------------------------------------------------------
// Following live
// ---------------------------------------------------------------------------

// Whether the list follows the newest line, and the rules for changing that:
//
//  * It follows by default.
//  * A drag (or the throw after it) that leaves the list away from the bottom stops following.
//  * A drag that returns to the bottom, the "Jump to live" button, and `kCaptionAutoLiveMs` with no
//    touch each resume it.
//
// Times are LVGL's tick in ms; the counter wraps, so differences are taken as unsigned.
class caption_follow {
public:
    bool following() const { return following_; }

    // A user's scroll ended up here. `at_bottom` is whether the view is within the slack of the bottom.
    // Returns true when the following changed.
    bool user_scrolled(bool at_bottom, uint32_t now_ms)
    {
        last_touch_ms_ = now_ms;
        return set(at_bottom);
    }

    // A finger is on the panel. Keeps the pause from timing out under it.
    void touched(uint32_t now_ms) { last_touch_ms_ = now_ms; }

    // The button. Returns true when the following changed.
    bool jump(uint32_t now_ms)
    {
        last_touch_ms_ = now_ms;
        return set(true);
    }

    // Called on every tick. Returns true when a pause has gone on long enough to end.
    bool tick(uint32_t now_ms)
    {
        if (following_ || now_ms - last_touch_ms_ < kCaptionAutoLiveMs) {
            return false;
        }
        following_ = true;
        return true;
    }

    // A screen that was just built follows.
    void reset(uint32_t now_ms)
    {
        following_ = true;
        last_touch_ms_ = now_ms;
    }

private:
    bool set(bool follow)
    {
        const bool changed = follow != following_;
        following_ = follow;
        return changed;
    }

    bool following_ = true;
    uint32_t last_touch_ms_ = 0;
};

// Whether the view, `bottom_px` of content below its edge, is at the bottom.
constexpr bool caption_at_bottom(int bottom_px)
{
    return bottom_px <= kCaptionBottomSlackPx;
}

// ---------------------------------------------------------------------------
// What is on the glass, against what the buffer holds
// ---------------------------------------------------------------------------

inline uint32_t caption_text_hash(const char *text)
{
    uint32_t h = 2166136261u;
    for (const auto *p = reinterpret_cast<const unsigned char *>(text); *p != '\0'; ++p) {
        h = (h ^ *p) * 16777619u;
    }
    return h;
}

// One line on the glass: the seq it came from and a hash of its text, which is how a changed line is told
// from the same line (a reset can reuse seqs).
struct caption_shown_line {
    uint64_t seq;
    uint32_t hash;
};

// What to do to the lines on the glass so they match the buffer.
struct caption_plan {
    bool rebuild;      // delete every line and add all of the buffer's
    int drop_front;    // else: delete this many from the top (the buffer's oldest were evicted)
    int append_from;   // and add the buffer's lines from this index on
    bool nothing() const { return !rebuild && drop_front == 0 && append_from < 0; }
};

// The common case is a line or two arriving, and the 201st line pushing the oldest out: that is an append and
// a drop, and keeps the list's scroll position steady. Anything else (a snapshot, a reset, a restarted Relay,
// a different feed) is a rebuild. The shown lines are compared with the buffer's by seq everywhere and by text
// at the two ends of the overlap; a mismatch is a rebuild, never a guess.
//
// `append_from` is -1 when the buffer has nothing new.
inline caption_plan caption_plan_update(const caption_shown_line *shown, int shown_count, const caption_buffer &buf)
{
    const int size = buf.size();
    const caption_plan rebuild = {true, 0, 0};
    if (shown_count <= 0 || size == 0) {
        return rebuild;
    }
    const caption_shown_line &newest = shown[shown_count - 1];
    int k = -1;  // where the newest shown line is in the buffer
    for (int i = size - 1; i >= 0; --i) {
        if (buf.at(i).seq == newest.seq) {
            k = i;
            break;
        }
    }
    if (k < 0 || caption_text_hash(buf.at(k).text) != newest.hash) {
        return rebuild;
    }
    int drop = 0;
    while (drop < shown_count && shown[drop].seq < buf.at(0).seq) {
        ++drop;
    }
    if (shown_count - drop != k + 1) {
        return rebuild;
    }
    for (int j = 0; j <= k; ++j) {
        if (shown[drop + j].seq != buf.at(j).seq) {
            return rebuild;
        }
    }
    if (caption_text_hash(buf.at(0).text) != shown[drop].hash) {
        return rebuild;
    }
    return {false, drop, k + 1 < size ? k + 1 : -1};
}

// ---------------------------------------------------------------------------
// The header
// ---------------------------------------------------------------------------

// The feed's name as the header shows it: "Transcription", or the translation's language as Relay names it
// ("SPANISH", "CHINESE_SIMPLIFIED") in words ("Spanish", "Chinese Simplified"). Returns the length.
inline size_t caption_feed_title(uint8_t stream, const char *lang, char *out, size_t cap)
{
    if (cap == 0) {
        return 0;
    }
    size_t o = 0;
    auto put = [&](char c) {
        if (o + 1 < cap) {
            out[o++] = c;
        }
    };
    if (stream == kFeedTranscription) {
        for (const char *p = "Transcription"; *p != '\0'; ++p) {
            put(*p);
        }
    } else if (stream == kFeedTranslation && lang != nullptr && lang[0] != '\0') {
        bool word_start = true;
        for (const char *p = lang; *p != '\0'; ++p) {
            char c = *p;
            if (c == '_' || c == '-') {
                c = ' ';
            }
            if (c == ' ') {
                word_start = true;
                put(c);
                continue;
            }
            if (c >= 'A' && c <= 'Z') {
                put(word_start ? c : static_cast<char>(c - 'A' + 'a'));
            } else if (c >= 'a' && c <= 'z') {
                put(word_start ? static_cast<char>(c - 'a' + 'A') : c);
            } else {
                put(c);
            }
            word_start = false;
        }
    } else if (stream == kFeedTranslation) {
        for (const char *p = "Translation"; *p != '\0'; ++p) {
            put(*p);
        }
    } else if (stream == kFeedService) {
        for (const char *p = "Service"; *p != '\0'; ++p) {
            put(*p);
        }
    }
    out[o] = '\0';
    return o;
}

enum class caption_tone : uint8_t { good, busy, bad, idle };

// The connection indicator: a coloured dot and a few words.
inline const char *caption_state_label(caption_state s)
{
    switch (s) {
    case caption_state::off: return "No feed";
    case caption_state::waiting_for_network: return "No network";
    case caption_state::connecting: return "Connecting";
    case caption_state::connected: return "Live";
    case caption_state::reconnecting: return "Reconnecting";
    case caption_state::relay_not_running: return "Relay not running";
    case caption_state::feed_refused: return "Feed not offered";
    }
    return "";
}

inline caption_tone caption_state_tone(caption_state s)
{
    switch (s) {
    case caption_state::connected: return caption_tone::good;
    case caption_state::connecting:
    case caption_state::reconnecting:
    case caption_state::waiting_for_network: return caption_tone::busy;
    case caption_state::relay_not_running:
    case caption_state::feed_refused: return caption_tone::bad;
    case caption_state::off: return caption_tone::idle;
    }
    return caption_tone::idle;
}

// What the empty list says, which depends on why it is empty.
inline const char *caption_empty_text(caption_state s)
{
    switch (s) {
    case caption_state::connected: return "Waiting for captions";
    case caption_state::off: return "No caption feed is chosen";
    case caption_state::relay_not_running: return "Relay is not running";
    case caption_state::feed_refused: return "Relay does not offer this feed";
    default: return "Connecting to Relay";
    }
}
