#pragma once

// The panel's stored configuration, and the rules for reading it back, with no ESP-IDF in
// it. `settings.cpp` reads and writes NVS and calls these; `test_native.cpp` compiles this
// header with g++ and checks the cases a blob from a different build, or a damaged one,
// will hand it.
//
// Copied from the VXP compact display's `settings_format.h` (vxp-compact-display 0.2.2),
// then changed: the VXP URL and room id are gone, and the Relay address override, the
// panel ID and the saved feed choice are in. See README.md, "Copied from VXP".

#include <cstddef>
#include <cstdint>
#include <cstring>

#include "panel_format.h"

// **One NVS blob, read and written whole.** The alternative was one NVS key per setting.
// That gives each field its own version and needs no layout check, but a save is then
// several writes, and a power cut between them leaves the Wi-Fi name from one
// configuration and its password from another. A blob is one NVS entry, and NVS writes an
// entry whole or not at all. The cost is that the layout is the struct, so a change to it
// has to be a change to `kSettingsSchema` and a rule for reading the old one.
struct relay_settings_t {
    uint32_t magic;          // identifies a blob this firmware wrote
    uint16_t schema;         // the layout version; see `kSettingsSchema`
    uint16_t rotation_deg;   // 0 or 180
    char wifi_ssid[33];      // 32 bytes and the terminator
    char wifi_password[65];  // 64 bytes and the terminator; empty for an open network
    char relay_addr[129];    // the Relay address override, `http://host[:port]`; empty means find Relay
    char panel_id[33];       // 32 lower-case hex characters, random, made on first boot
    uint8_t ip_mode;         // kIpModeDhcp or kIpModeStatic; zero is DHCP
    uint8_t feed_stream;     // the saved feed: kFeedNone, kFeedTranscription, kFeedTranslation or kFeedService
    uint8_t reserved[2];     // zero
    char feed_lang[20];      // the translation feed's language, as Relay names it ("SPANISH")
    uint8_t static_addr[4];     // the station's IPv4 address, network byte order
    uint8_t static_mask[4];     // its subnet mask
    uint8_t static_gateway[4];  // the default gateway
    uint8_t static_dns1[4];     // the primary DNS server
    uint8_t static_dns2[4];     // the secondary DNS server; 0.0.0.0 when there is none
    // Schema 2: the split view. Pane 1 is `feed_stream`/`feed_lang` above; pane 2 is here.
    uint8_t feed_split;      // 0 for one feed full screen, 1 for two side by side
    uint8_t feed2_stream;    // pane 2's feed: kFeedNone, kFeedTranscription or kFeedTranslation
    uint8_t reserved2[2];    // zero
    char feed2_lang[20];     // pane 2's translation language, as Relay names it
};

// The layout is pinned: a compiler that pads this differently would read the blobs
// another build wrote as garbage. 336 bytes, no padding. Schema 1 was the first 312 of them.
static_assert(sizeof(relay_settings_t) == 336, "relay_settings_t layout changed; bump kSettingsSchema");

// The size of a schema 1 blob: everything up to `feed_split`. A panel flashed with this firmware over one
// that wrote schema 1 keeps its network, panel ID and feed: the split fields read as zero.
constexpr size_t kSettingsSizeV1 = 312;

// "RLDP" in ASCII, little-endian in the blob. A blob that does not start with this was not
// written by this firmware and is not read.
constexpr uint32_t kSettingsMagic = 0x50444C52;

// The layout version. Bump it when the struct changes, and teach `settings_decode` to read
// the version before it. A blob this firmware does not know is replaced with defaults, not
// read as the wrong fields.
constexpr uint16_t kSettingsSchema = 2;

// How the station gets its address. DHCP is the default: a zeroed blob is DHCP.
constexpr uint8_t kIpModeDhcp = 0;
constexpr uint8_t kIpModeStatic = 1;

// The saved feed: what the panel shows. None until someone chooses.
constexpr uint8_t kFeedNone = 0;
constexpr uint8_t kFeedTranscription = 1;
constexpr uint8_t kFeedTranslation = 2;
// The Planning Center service (#82): `feed_lang` holds the service type id.
constexpr uint8_t kFeedService = 3;

// What `settings_decode` made of a blob.
enum class settings_blob {
    accepted,        // read whole; `*out` now holds it
    rotation_reset,  // read, but its rotation was not 0 or 180 and is the default now
    wrong_size,      // not the size of this layout
    wrong_magic,     // not written by this firmware
    wrong_schema,    // written by a layout this firmware does not read
};

// Fill `out` with nothing configured: the magic and schema this firmware writes, empty text
// fields, DHCP, no feed, and `rotation_deg` as given.
inline void settings_defaults_fill(relay_settings_t *out, uint16_t rotation_deg)
{
    memset(out, 0, sizeof *out);
    out->magic = kSettingsMagic;
    out->schema = kSettingsSchema;
    out->rotation_deg = rotation_deg;
    out->ip_mode = kIpModeDhcp;
    out->feed_stream = kFeedNone;
    out->feed_split = 0;
    out->feed2_stream = kFeedNone;
}

// Copy `src` into a text field of `capacity` bytes. What does not fit is cut off, and the
// field always ends in a terminator, so a long value can never run past its field. A
// `capacity` of zero writes nothing.
inline void settings_set_text(char *field, size_t capacity, const char *src)
{
    if (capacity == 0) {
        return;
    }
    size_t n = 0;
    while (n + 1 < capacity && src[n] != '\0') {
        ++n;
    }
    memcpy(field, src, n);
    field[n] = '\0';
}

// The panel ID from 16 random bytes: 32 lower-case hex characters. The ID names the panel
// to Relay. It carries no meaning, so a panel that is reset and gets a new one is a new
// panel as far as Relay is concerned.
inline void settings_panel_id_from_bytes(const uint8_t bytes[16], char out[33])
{
    static const char kHex[] = "0123456789abcdef";
    for (int i = 0; i < 16; ++i) {
        out[i * 2] = kHex[bytes[i] >> 4];
        out[i * 2 + 1] = kHex[bytes[i] & 0x0F];
    }
    out[32] = '\0';
}

// Whether `id` is a panel ID this firmware made: exactly 32 lower-case hex characters.
inline bool settings_panel_id_valid(const char *id)
{
    for (int i = 0; i < 32; ++i) {
        const char c = id[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
            return false;
        }
    }
    return id[32] == '\0';
}

// Read `len` bytes of a stored blob into `*out`. `*out` is written only when the blob is
// accepted, so a caller that filled it with defaults first keeps those on a refusal. The
// strings are terminated in place, whatever the blob holds, and a rotation that is neither
// 0 nor 180 becomes `default_rotation`: the orientation the build was configured for, so a
// damaged value falls back to what the board would have used with nothing stored. The
// caller passes it in because this header holds no CONFIG_ macros.
inline settings_blob settings_decode(const void *blob, size_t len, relay_settings_t *out,
                                     uint16_t default_rotation)
{
    if (blob == nullptr || (len != sizeof(relay_settings_t) && len != kSettingsSizeV1)) {
        return settings_blob::wrong_size;
    }
    relay_settings_t stored;
    memset(&stored, 0, sizeof stored);
    memcpy(&stored, blob, len);
    if (stored.magic != kSettingsMagic) {
        return settings_blob::wrong_magic;
    }
    // A schema 1 blob is its own size, and a blob of either size that names the other schema is not read.
    const bool v1 = len == kSettingsSizeV1;
    if (stored.schema != (v1 ? 1 : kSettingsSchema)) {
        return settings_blob::wrong_schema;
    }
    stored.schema = kSettingsSchema;

    // The strings are bounded by their fields, but a stored value still gets its terminator
    // forced in, so a corrupt blob cannot run a string past its field.
    stored.wifi_ssid[sizeof stored.wifi_ssid - 1] = '\0';
    stored.wifi_password[sizeof stored.wifi_password - 1] = '\0';
    stored.relay_addr[sizeof stored.relay_addr - 1] = '\0';
    stored.panel_id[sizeof stored.panel_id - 1] = '\0';
    stored.feed_lang[sizeof stored.feed_lang - 1] = '\0';
    stored.feed2_lang[sizeof stored.feed2_lang - 1] = '\0';

    // An address mode that is neither DHCP nor static is treated as DHCP, the default a
    // board with nothing stored gets. A damaged byte must not select an address the user
    // did not choose. The same for a feed: an unknown one is no feed.
    if (stored.ip_mode != kIpModeDhcp && stored.ip_mode != kIpModeStatic) {
        stored.ip_mode = kIpModeDhcp;
    }
    if (stored.feed_stream > kFeedService) {
        stored.feed_stream = kFeedNone;
    }
    if (stored.feed2_stream > kFeedService) {
        stored.feed2_stream = kFeedNone;
    }
    if (stored.feed_split > 1) {
        stored.feed_split = 0;
    }
    // A panel ID that is not one this firmware made is dropped; boot makes a new one.
    if (!settings_panel_id_valid(stored.panel_id)) {
        stored.panel_id[0] = '\0';
    }

    settings_blob result = settings_blob::accepted;
    if (!panel_rotation_valid(stored.rotation_deg)) {
        stored.rotation_deg = default_rotation;
        result = settings_blob::rotation_reset;
    }
    *out = stored;
    return result;
}
