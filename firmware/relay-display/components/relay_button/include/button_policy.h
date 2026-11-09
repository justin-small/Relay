#pragma once

// When a press of the BOOT button counts as a request to open the setup network, and when the same
// press, held on, counts as a factory reset. Shared since #1480: the I/O board takes the factory reset
// at the same fifteen seconds, so one gesture resets every VXP device, and ignores `recover`. Pure: the
// caller reads the pin and the monotonic clock and passes them in, so `test_native.cpp` drives it
// with a fake clock and a scripted level, and no ESP-IDF is needed.

#include <cstdint>

namespace button {

// A press must be held this long, measured from the start of the debounced press, before it counts.
// Five seconds is long enough that a person does it on purpose: a brush against the board or a
// tap for a reset does not hold a button that long. The alternative was a tap pattern, which a
// person can press by accident in a run of taps and which the bench would have to teach.
constexpr int64_t kRecoverHoldMs = 5000;

// **Held on to fifteen seconds, the same press is a factory reset.** Recovery at five keeps the stored network
// and the VXP address, which is what the panel's own hints ask for when the network or the certificate needs
// changing. A reset that wipes everything is a separate, rarer intent, so it is the same press held three times as
// long rather than a second gesture to learn. The setup network the five-second mark opened is wiped with the rest.
// The alternative was a full wipe at five seconds, which would make every hint the panel prints cost the installer
// the network and the address too.
constexpr int64_t kFactoryResetHoldMs = 15000;

// What a sample reports. Each press reports `recover` at most once and then `factory_reset` at most once.
enum class Event { none, recover, factory_reset };

// A level is believed only after it has been stable this long. A mechanical switch bounces for a few
// milliseconds on every press and release, and a bounce must not start or end a hold.
constexpr int64_t kDebounceMs = 50;

// One button, sampled at a steady rate by the caller. `sample` takes the time in milliseconds on the
// monotonic clock and whether the button is pressed. It returns `Event::recover` once per press, on the sample at
// which the hold reaches `kRecoverHoldMs`, and `Event::factory_reset` once, on the sample at which the same hold
// reaches `kFactoryResetHoldMs`.
//
// **A press that is already down at the first sample does not count until it has been released.**
// GPIO0 is a strapping pin: holding BOOT across a reset selects the ROM's download mode, so a button
// that is down at power-on belongs to the bootloader, not to this firmware. The first sample is taken
// as the level without debounce, and a press it finds is not armed. Without this a bootloader press
// that a person releases a moment later would open the setup network as the firmware starts.
//
// **One press fires each event once.** Holding the button for thirty seconds opens the setup network once and resets
// once, not every five seconds. A release arms the button again.
class Hold {
public:
    Event sample(int64_t now_ms, bool pressed)
    {
        if (!started_) {
            started_ = true;
            level_ = pressed;
            raw_ = pressed;
            raw_since_ms_ = now_ms;
            held_since_ms_ = now_ms;
            armed_ = !pressed;
            reset_armed_ = !pressed;
            return Event::none;
        }

        // Track the raw level and when it last changed. A flip back before the debounce window closes
        // restarts `raw_since_ms_` and leaves `level_` alone, so the hold is not reset by a bounce.
        if (pressed != raw_) {
            raw_ = pressed;
            raw_since_ms_ = now_ms;
        }
        if (raw_ != level_ && now_ms - raw_since_ms_ >= kDebounceMs) {
            level_ = raw_;
            if (level_) {
                held_since_ms_ = raw_since_ms_;
            } else {
                armed_ = true;
                reset_armed_ = true;
            }
        }

        if (level_ && reset_armed_ && now_ms - held_since_ms_ >= kFactoryResetHoldMs) {
            reset_armed_ = false;
            return Event::factory_reset;
        }
        if (level_ && armed_ && now_ms - held_since_ms_ >= kRecoverHoldMs) {
            armed_ = false;
            return Event::recover;
        }
        return Event::none;
    }

private:
    bool started_ = false;
    bool level_ = false;        // the debounced level: true while the button is down
    bool raw_ = false;          // the level as last sampled
    bool armed_ = false;        // a press may recover; cleared by the event, and by a press held at boot
    bool reset_armed_ = false;  // a press may reset; cleared by the event, and by a press held at boot
    int64_t raw_since_ms_ = 0;  // when `raw_` last changed
    int64_t held_since_ms_ = 0; // when the debounced press began, on the raw change that started it
};

}  // namespace button
