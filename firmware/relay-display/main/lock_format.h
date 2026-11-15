#pragma once

// The operator's lock on a panel (#126), with no ESP-IDF in it, so `test_native.cpp` checks it on a laptop.
// `panel_lock.cpp` keeps it, saves it and answers the menu; `caption_view.cpp` draws the refusal and the keypad.
//
// **What Relay sends** (`app/panels.py`, `Registry.policy`), in each check-in reply's `policy` and on the event stream
// when the operator changes it:
//
//   {"type":"panel_policy","lock":true,"pin":"2468","unlock_s":300}
//
// A locked panel refuses every change made at its screen (the feeds, one or two of them, and the rotation): the menu
// is still shown, and a change is refused with a short note. With a `pin` ("" for none), the menu offers a keypad; the
// right PIN unlocks the screen until it has gone `unlock_s` with no touch, and then it locks again by itself. Five
// wrong PINs in a row make the keypad wait a minute. What Relay sets (`set_feed`, `set_rotation`) is never refused.
//
// **The return to the operator's choice is Relay's** (it sends the operator's feed again as a `set_feed`), so all the
// panel does for it is say on each check-in how long its screen has gone untouched (`idle_s`).

#include <cstddef>
#include <cstdint>
#include <cstring>

#include "caption_format.h"

constexpr size_t kLockPinMin = 4;
constexpr size_t kLockPinMax = 8;
constexpr uint32_t kLockUnlockMinS = 60;
constexpr uint32_t kLockUnlockMaxS = 3600;
constexpr uint32_t kLockUnlockDefaultS = 300;
constexpr uint32_t kLockTriesBeforeWait = 5;
constexpr uint32_t kLockWaitMs = 60000;

struct panel_policy {
    bool lock = false;
    char pin[kLockPinMax + 1] = {};  // digits only; empty for none
    uint32_t unlock_s = kLockUnlockDefaultS;
};

inline bool lock_pin_valid(const char *pin)
{
    const size_t n = strlen(pin);
    if (n < kLockPinMin || n > kLockPinMax) {
        return false;
    }
    for (size_t i = 0; i < n; ++i) {
        if (pin[i] < '0' || pin[i] > '9') {
            return false;
        }
    }
    return true;
}

inline bool lock_policy_equal(const panel_policy &a, const panel_policy &b)
{
    return a.lock == b.lock && a.unlock_s == b.unlock_s && strcmp(a.pin, b.pin) == 0;
}

// The object at `obj`: a `panel_policy` event, or a reply's `policy`. False, and `out` untouched, for anything the
// panel will not act on: no `lock`, a PIN that is not 4 to 8 digits, an unlock time outside a minute to an hour. A
// missing `unlock_s` is the default, and a missing `pin` is none.
inline bool lock_policy_parse(const char *obj, const char *e, panel_policy *out)
{
    panel_policy got;
    const char *l = caption_json::get(obj, e, "lock");
    if (l == nullptr || !caption_json::read_bool(l, e, &got.lock)) {
        return false;
    }
    const char *p = caption_json::get(obj, e, "pin");
    if (p != nullptr) {
        // `read_string` stops quietly at the buffer's end, so a longer PIN is caught by the quoted value's own length.
        const char *p_end = caption_json::skip_value(p, e);
        size_t n = 0;
        if (p_end == nullptr || static_cast<size_t>(p_end - p) > kLockPinMax + 2 ||
            !caption_json::read_string(p, e, got.pin, sizeof got.pin, &n)) {
            return false;
        }
        if (n != 0 && !lock_pin_valid(got.pin)) {
            return false;
        }
    }
    const char *u = caption_json::get(obj, e, "unlock_s");
    if (u != nullptr) {
        uint64_t s = 0;
        if (!caption_json::read_uint(u, e, &s) || s < kLockUnlockMinS || s > kLockUnlockMaxS) {
            return false;
        }
        got.unlock_s = static_cast<uint32_t>(s);
    }
    *out = got;
    return true;
}

// What the keypad's OK says.
enum class lock_try : uint8_t {
    unlocked,  // the right PIN: the screen is open until it goes `unlock_s` untouched
    wrong,     // not the PIN
    wait,      // too many wrong in a row: the keypad waits (`lock_state::wait_left_ms`)
    no_pin,    // the operator set none, so only Relay can unlock it
    open,      // not locked at all
};

// The lock as the screen sees it. Times are the screen's own milliseconds (LVGL's tick); `idle_ms` is how long the
// screen has gone untouched.
struct lock_state {
    panel_policy policy;
    bool unlocked = false;   // by the PIN, until the screen goes untouched for `policy.unlock_s`
    uint32_t wrong = 0;      // wrong PINs in a row
    uint32_t wait_from = 0;  // when the wait began, while `wrong` is at the limit

    // A new word from Relay. A change to the lock or the PIN ends an unlock made with the old one; the same policy
    // again (it comes on every check-in) changes nothing.
    void set(const panel_policy &p)
    {
        if (lock_policy_equal(p, policy)) {
            return;
        }
        if (p.lock != policy.lock || strcmp(p.pin, policy.pin) != 0) {
            unlocked = false;
            wrong = 0;
        }
        policy = p;
    }

    // Lock again once the screen has gone untouched for the unlock time. Call from the screen's tick.
    void tick(uint32_t idle_ms)
    {
        if (unlocked && idle_ms >= policy.unlock_s * 1000u) {
            unlocked = false;
        }
    }

    // Whether a change at the screen is refused now.
    bool refuses() const { return policy.lock && !unlocked; }

    bool has_pin() const { return policy.pin[0] != '\0'; }

    uint32_t wait_left_ms(uint32_t now) const
    {
        if (wrong < kLockTriesBeforeWait) {
            return 0;
        }
        const uint32_t since = now - wait_from;
        return since >= kLockWaitMs ? 0 : kLockWaitMs - since;
    }

    lock_try try_pin(const char *entered, uint32_t now)
    {
        if (!policy.lock) {
            return lock_try::open;
        }
        if (!has_pin()) {
            return lock_try::no_pin;
        }
        if (wait_left_ms(now) > 0) {
            return lock_try::wait;
        }
        if (wrong >= kLockTriesBeforeWait) {
            wrong = 0;  // the wait is over: a fresh set of tries
        }
        // Every digit is compared, whatever the first wrong one, so the time taken does not say how much was right.
        const size_t a = strlen(entered);
        const size_t b = strlen(policy.pin);
        unsigned diff = a != b ? 1u : 0u;
        for (size_t i = 0; i < kLockPinMax; ++i) {
            const char x = i < a ? entered[i] : '\0';
            const char y = i < b ? policy.pin[i] : '\0';
            diff |= static_cast<unsigned>(x ^ y);
        }
        if (diff == 0) {
            unlocked = true;
            wrong = 0;
            return lock_try::unlocked;
        }
        if (++wrong >= kLockTriesBeforeWait) {
            wait_from = now;
            return lock_try::wait;
        }
        return lock_try::wrong;
    }
};

// The NVS copy of the policy, so a panel that restarts while Relay is away stays locked. `version` guards the layout.
struct lock_blob {
    uint8_t version;
    uint8_t lock;
    char pin[kLockPinMax + 1];
    uint32_t unlock_s;
};
constexpr uint8_t kLockBlobVersion = 1;

inline lock_blob lock_to_blob(const panel_policy &p)
{
    lock_blob b = {};
    b.version = kLockBlobVersion;
    b.lock = p.lock ? 1 : 0;
    memcpy(b.pin, p.pin, sizeof b.pin);
    b.pin[kLockPinMax] = '\0';
    b.unlock_s = p.unlock_s;
    return b;
}

// False, and `out` untouched, for a blob of another version or one that does not hold a policy Relay could have sent.
inline bool lock_from_blob(const lock_blob &b, panel_policy *out)
{
    if (b.version != kLockBlobVersion || b.lock > 1 || b.unlock_s < kLockUnlockMinS || b.unlock_s > kLockUnlockMaxS ||
        memchr(b.pin, '\0', sizeof b.pin) == nullptr || (b.pin[0] != '\0' && !lock_pin_valid(b.pin))) {
        return false;
    }
    panel_policy p;
    p.lock = b.lock == 1;
    memcpy(p.pin, b.pin, sizeof p.pin);
    p.unlock_s = b.unlock_s;
    *out = p;
    return true;
}
