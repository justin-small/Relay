#pragma once

// What an application image's first bytes say, and what proves a new image works. No ESP-IDF in it, so
// `test_native.cpp` checks it on a laptop. `firmware_update.cpp` does the flash and the network.
//
// Copied from the VXP firmware's `components/vxp_ota/include/ota.h` (README.md, "Copied from VXP"), cut
// down to what a panel that fetches its own image needs: there is no upload page here, so the verdicts
// that answered an HTTP upload are gone.
//
// **The point of reading the head first** is that a board that accepts the wrong file writes it into an
// application slot and fails to boot it a download later. Deciding from the first 112 bytes, before a
// slot is opened, turns that into a line in the log and a word on Relay's admin page.

#include <cstddef>
#include <cstdint>

// Every ESP-IDF image, bootloader and application alike, starts with this.
constexpr uint8_t kOtaImageMagic = 0xE9;

// `esp_app_desc_t.magic_word`, little-endian, behind the 24-byte image header and the 8-byte first
// segment header. An application carries it; a bootloader carries a different word at the same offset.
constexpr uint32_t kOtaAppDescMagic = 0xABCD5432u;
constexpr size_t kOtaAppDescMagicOffset = 32;

// `esp_app_desc_t.version`: 32 bytes of NUL-padded text behind the magic word, `secure_version` and two
// reserved words. `CMakeLists.txt` sets it from `RELAY_FW_VERSION`.
constexpr size_t kOtaVersionOffset = kOtaAppDescMagicOffset + 16;
constexpr size_t kOtaVersionBytes = 32;

// `esp_app_desc_t.project_name`, directly behind the version: `relay-display-app`, which names the
// hardware target. An image for another board is refused on it.
constexpr size_t kOtaProjectOffset = kOtaVersionOffset + kOtaVersionBytes;
constexpr size_t kOtaProjectBytes = 32;

// How much of an image has to be in hand to decide anything about it.
constexpr size_t kOtaHeadBytes = kOtaProjectOffset + kOtaProjectBytes;

enum class ota_verdict : uint8_t {
    accept,
    not_an_image,  // not an ESP-IDF image at all, or too short to say
    merged_image,  // an ESP-IDF image whose descriptor is not an application's: the serial-flash file
    older,         // an older release than the one running, or a version that cannot be read
};

inline ota_verdict ota_inspect_head(const uint8_t *head, size_t len)
{
    if (head == nullptr || len < kOtaHeadBytes || head[0] != kOtaImageMagic) {
        return ota_verdict::not_an_image;
    }
    const uint32_t magic = static_cast<uint32_t>(head[kOtaAppDescMagicOffset]) |
                           (static_cast<uint32_t>(head[kOtaAppDescMagicOffset + 1]) << 8) |
                           (static_cast<uint32_t>(head[kOtaAppDescMagicOffset + 2]) << 16) |
                           (static_cast<uint32_t>(head[kOtaAppDescMagicOffset + 3]) << 24);
    return magic == kOtaAppDescMagic ? ota_verdict::accept : ota_verdict::merged_image;
}

// `X.Y.Z` out of at most `cap` bytes, with a NUL, `-` or `+` after it. False for anything else.
inline bool ota_parse_version(const char *text, size_t cap, unsigned out[3])
{
    if (text == nullptr) {
        return false;
    }
    size_t i = 0;
    for (int part = 0; part < 3; ++part) {
        if (part > 0) {
            if (i >= cap || text[i] != '.') {
                return false;
            }
            ++i;
        }
        if (i >= cap || text[i] < '0' || text[i] > '9') {
            return false;
        }
        unsigned value = 0;
        while (i < cap && text[i] >= '0' && text[i] <= '9') {
            if (value > 99999u) {
                return false;
            }
            value = value * 10u + static_cast<unsigned>(text[i] - '0');
            ++i;
        }
        out[part] = value;
    }
    if (i >= cap) {
        return false;
    }
    return text[i] == '\0' || text[i] == '-' || text[i] == '+';
}

// Whether an image goes forward. **Every signature a panel has ever trusted is still valid**, so without
// this a signed update is also a signed downgrade, to whatever the newer release fixed. The same version
// again is allowed (reinstalling what runs repairs a slot). A version that does not parse is refused; a
// running version that does not parse is waved through, because refusing would strand every panel.
inline ota_verdict ota_inspect_version(const uint8_t *head, size_t len, const char *running)
{
    if (head == nullptr || len < kOtaHeadBytes) {
        return ota_verdict::not_an_image;
    }
    unsigned have[3] = {0, 0, 0};
    if (!ota_parse_version(running, kOtaVersionBytes, have)) {
        return ota_verdict::accept;
    }
    unsigned incoming[3] = {0, 0, 0};
    if (!ota_parse_version(reinterpret_cast<const char *>(head) + kOtaVersionOffset, kOtaVersionBytes, incoming)) {
        return ota_verdict::older;
    }
    for (int part = 0; part < 3; ++part) {
        if (incoming[part] != have[part]) {
            return incoming[part] > have[part] ? ota_verdict::accept : ota_verdict::older;
        }
    }
    return ota_verdict::accept;
}

// Whether the descriptor names `expected` as its project, exactly: the whole field's text, NUL after.
inline bool ota_project_matches(const uint8_t *head, size_t len, const char *expected)
{
    if (head == nullptr || expected == nullptr || len < kOtaHeadBytes) {
        return false;
    }
    const uint8_t *name = head + kOtaProjectOffset;
    size_t i = 0;
    while (i < kOtaProjectBytes && expected[i] != '\0') {
        if (name[i] != static_cast<uint8_t>(expected[i])) {
            return false;
        }
        ++i;
    }
    if (expected[i] != '\0') {
        return false;
    }
    for (; i < kOtaProjectBytes; ++i) {
        if (name[i] != 0) {
            return false;
        }
    }
    return true;
}

// Whether the descriptor's version is exactly `version` (the one Relay's offer named): the image is the
// release it was offered as.
inline bool ota_version_matches(const uint8_t *head, size_t len, const char *version)
{
    if (head == nullptr || version == nullptr || len < kOtaHeadBytes) {
        return false;
    }
    const uint8_t *field = head + kOtaVersionOffset;
    size_t i = 0;
    while (i < kOtaVersionBytes && version[i] != '\0') {
        if (field[i] != static_cast<uint8_t>(version[i])) {
            return false;
        }
        ++i;
    }
    if (version[i] != '\0') {
        return false;
    }
    for (; i < kOtaVersionBytes; ++i) {
        if (field[i] != 0) {
            return false;
        }
    }
    return true;
}

// **What proves a new image works: it reached Relay.** A freshly written image boots once on approval and
// has to say so. The proof is what an update could plausibly have broken and nothing else: it booted (the
// caller only asks while pending), NVS read back the configuration it had, and Relay answered a check-in
// with a 200. A panel nothing ever reaches never confirms, and the bootloader takes the image back, which
// is the right direction to fail in: an image nobody can reach is not one to keep.
inline bool ota_confirmed_by(bool pending_verify, bool settings_readable, bool reached_relay)
{
    return pending_verify && settings_readable && reached_relay;
}
