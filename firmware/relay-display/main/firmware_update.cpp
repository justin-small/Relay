#include "firmware_update.h"

#include "sdkconfig.h"

// The two refusals below depend on these symbols. A renamed one after a toolchain bump must fail the build,
// not leave the panel accepting unsigned images with nothing in the output that a reader would stop at.
#if !defined(CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT)
#error "Update signature verification is not configured: CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT is missing. See sdkconfig.defaults."
#endif
#if !defined(CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE)
#error "Rollback is not configured: CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is missing. Without it a bad image is a site visit. See sdkconfig.defaults."
#endif

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>

#include "captions.h"
#include "config.h"
#include "display.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "feeds.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "mbedtls/sha256.h"
#include "ota_format.h"
#include "panel_link_format.h"
#include "settings.h"
#include "update_format.h"

// In the order it happens: Relay hands over an order (`offer`); the task fetches the image, checks its head,
// writes the spare slot, checks the checksum and the signature, and keeps the verified image until the panel is
// at a quiet moment or the order says now. Then it points the bootloader at the slot and restarts. On the next
// boot the image is on approval until it reaches Relay with a 200, and the bootloader takes it back if it does
// not in time.

namespace {

const char *TAG = "update";

constexpr uint32_t kStackBytes = 8192;
constexpr int64_t kSecondUs = 1000 * 1000;

using update_format::update_error;
using update_format::update_state;

struct Shared {
    update_format::Order order;
    update_state state;
    update_error error;
    bool pending;            // this boot is a new image awaiting its proof
    bool settings_readable;
    int64_t boot_us;
    int64_t line_us;         // the last caption line, for the quiet moment
    bool staged;             // a verified image is in `staged_part`, waiting for its restart
    const esp_partition_t *staged_part;
    char staged_version[update_format::kVersionBytes];
    char rolled_back[update_format::kVersionBytes];  // taken back at boot; not fetched again until Relay has heard
    uint32_t failures;
    int64_t retry_at_us;
    char panel_id[33];
};

Shared s_ = {};
SemaphoreHandle_t s_lock = nullptr;

class Guard {
public:
    Guard() { xSemaphoreTake(s_lock, portMAX_DELAY); }
    ~Guard() { xSemaphoreGive(s_lock); }
    Guard(const Guard &) = delete;
    Guard &operator=(const Guard &) = delete;
};

int64_t now_us()
{
    return esp_timer_get_time();
}

bool copy_text(char *dst, size_t cap, const char *src)
{
    if (src == nullptr || std::strlen(src) >= cap) {
        return false;
    }
    std::strcpy(dst, src);
    return true;
}

// ---------------------------------------------------------------------------
// Telling Relay
// ---------------------------------------------------------------------------

// One POST of an update report. Returns the HTTP status Relay answered, 0 when it never did. Best effort: a
// report that does not arrive is not worth stopping an update for, and the outcome ones are retried.
int post_report(const char *state, const char *version, unsigned progress, const char *error)
{
    char url[sizeof(relay_settings_t::relay_addr)];
    char host[sizeof(relay_settings_t::relay_addr)];
    uint16_t port = 0;
    if (!captions_has_address() || !feeds_relay_url(url, sizeof url) || !caption_url_split(url, host, sizeof host, &port)) {
        return 0;
    }
    char body[160];
    const size_t body_len = update_format::report_body(body, sizeof body, state, version, progress, error);
    char request[480];
    char id[33];
    {
        Guard guard;
        std::snprintf(id, sizeof id, "%s", s_.panel_id);
    }
    const size_t request_len = body_len > 0 ? panel_update_report_request(request, sizeof request, host, port, id, body,
                                                                         body_len, RELAY_FW_VERSION)
                                            : 0;
    if (request_len == 0) {
        return 0;
    }
    const int fd = captions_connect(host, port);
    if (fd < 0) {
        return 0;
    }
    int status = 0;
    auto *http = new (std::nothrow) caption_http();
    if (http != nullptr && captions_send(fd, request, request_len)) {
        char buf[256];
        const int64_t deadline = now_us() + 5 * kSecondUs;
        while (now_us() < deadline) {
            const ssize_t n = recv(fd, buf, sizeof buf, 0);
            if (n > 0) {
                if (!http->feed(buf, static_cast<size_t>(n), [](void *, const char *, size_t) {}, nullptr) ||
                    http->status() != 0) {
                    break;
                }
                continue;
            }
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                continue;
            }
            break;
        }
        status = http->status();
    }
    delete http;
    shutdown(fd, SHUT_RDWR);
    close(fd);
    return status;
}

void report(update_state state, const char *version, unsigned progress, update_error error)
{
    post_report(update_format::state_word(state), version, progress, update_format::error_word(error));
}

// An outcome the last boot left (`installed` or `rolled_back`) that Relay has not heard. Sent until Relay
// answers 200, then the record is cleared.
void report_outcome_if_any()
{
    update_record_t record = {};
    settings_load_update(&record);
    if (record.pending[0] == '\0') {
        return;
    }
    char word[16] = {};
    char version[update_format::kVersionBytes] = {};
    if (std::sscanf(record.pending, "%15s %32s", word, version) != 2) {
        record.pending[0] = '\0';
        settings_save_update(record);
        return;
    }
    if (post_report(word, version, 100, "") == 200) {
        record.pending[0] = '\0';
        settings_save_update(record);
        Guard guard;
        s_.rolled_back[0] = '\0';  // Relay has the word and has ended its ask; the version may be offered afresh
        ESP_LOGI(TAG, "update: Relay has been told: %s %s", word, version);
    }
}

// ---------------------------------------------------------------------------
// The download
// ---------------------------------------------------------------------------

struct Sink {
    const update_format::Order *order;
    uint8_t head[kOtaHeadBytes];
    size_t head_len;
    bool begun;
    esp_ota_handle_t handle;
    const esp_partition_t *target;
    uint8_t stage[update_format::kChunkBytes];
    size_t stage_len;
    mbedtls_sha256_context sha;
    size_t total;
    update_error failure;
};

bool flush(Sink &k, const uint8_t *data, size_t n)
{
    if (n == 0) {
        return true;
    }
    const bool ok = esp_ota_write(k.handle, data, n) == ESP_OK;
    display_resync();  // each write is a flash write, and the picture is put back after it
    if (!ok) {
        k.failure = update_error::install;
        return false;
    }
    mbedtls_sha256_update(&k.sha, data, n);
    return true;
}

// The head is decided as soon as it is in, in the order that refuses soonest, before a slot is opened.
bool decide_head(Sink &k)
{
    ota_verdict v = ota_inspect_head(k.head, k.head_len);
    if (v != ota_verdict::accept) {
        ESP_LOGW(TAG, "update: refused (%s)", update_format::error_word(update_format::error_for(v)));
        k.failure = update_format::error_for(v);
        return false;
    }
    if (!ota_project_matches(k.head, k.head_len, esp_app_get_description()->project_name)) {
        ESP_LOGW(TAG, "update: refused (compatible): the image is for other hardware");
        k.failure = update_error::compatible;
        return false;
    }
    if (!ota_version_matches(k.head, k.head_len, k.order->version)) {
        ESP_LOGW(TAG, "update: refused (download): the image is not the release Relay named");
        k.failure = update_error::download;
        return false;
    }
    v = ota_inspect_version(k.head, k.head_len, RELAY_FW_VERSION);
    if (v != ota_verdict::accept) {
        ESP_LOGW(TAG, "update: refused (version)");
        k.failure = update_format::error_for(v);
        return false;
    }
    if (esp_ota_begin(k.target, OTA_WITH_SEQUENTIAL_WRITES, &k.handle) != ESP_OK) {
        k.failure = update_error::install;
        return false;
    }
    k.begun = true;
    mbedtls_sha256_starts(&k.sha, 0);
    return flush(k, k.head, k.head_len);
}

void on_body(void *ctx, const char *p, size_t n)
{
    auto &k = *static_cast<Sink *>(ctx);
    if (k.failure != update_error::none) {
        return;
    }
    if (k.total + n > k.order->size) {
        k.failure = update_error::download;  // more than Relay said the image was
        return;
    }
    k.total += n;
    const auto *bytes = reinterpret_cast<const uint8_t *>(p);
    while (n > 0 && k.failure == update_error::none) {
        if (k.head_len < kOtaHeadBytes) {
            const size_t take = (kOtaHeadBytes - k.head_len) < n ? (kOtaHeadBytes - k.head_len) : n;
            std::memcpy(k.head + k.head_len, bytes, take);
            k.head_len += take;
            bytes += take;
            n -= take;
            if (k.head_len == kOtaHeadBytes && !decide_head(k)) {
                return;
            }
            continue;
        }
        const size_t room = update_format::kChunkBytes - k.stage_len;
        const size_t take = room < n ? room : n;
        std::memcpy(k.stage + k.stage_len, bytes, take);
        k.stage_len += take;
        bytes += take;
        n -= take;
        if (k.stage_len == update_format::kChunkBytes) {
            flush(k, k.stage, k.stage_len);
            k.stage_len = 0;
        }
    }
}

// The download, end to end. `none` when a verified image is staged for its restart, the reason otherwise. An
// aborted write is aborted here, before the caller decides anything.
update_error download(const update_format::Order &order)
{
    char url[sizeof(relay_settings_t::relay_addr)];
    char host[sizeof(relay_settings_t::relay_addr)];
    uint16_t port = 0;
    if (!captions_has_address() || !feeds_relay_url(url, sizeof url) || !caption_url_split(url, host, sizeof host, &port)) {
        return update_error::download;
    }
    char request[360];
    if (panel_image_request(request, sizeof request, host, port, order.path, RELAY_FW_VERSION) == 0) {
        return update_error::download;
    }
    // On the heap for the length of this download and no longer: a static copy would hold 4 KB of internal RAM
    // for the life of the panel.
    auto *k = static_cast<Sink *>(std::calloc(1, sizeof(Sink)));
    auto *http = new (std::nothrow) caption_http();
    if (k == nullptr || http == nullptr) {
        std::free(k);
        delete http;
        ESP_LOGW(TAG, "update: no memory for a download");
        return update_error::download;
    }
    k->order = &order;
    k->target = esp_ota_get_next_update_partition(nullptr);
    mbedtls_sha256_init(&k->sha);
    update_error result = update_error::none;
    if (k->target == nullptr || order.size > k->target->size) {
        result = k->target == nullptr ? update_error::install : update_error::download;
    } else {
        const int fd = captions_connect(host, port);
        if (fd < 0 || !captions_send(fd, request, std::strlen(request))) {
            if (fd >= 0) {
                close(fd);
            }
            result = update_error::download;
        } else {
            char buf[1024];
            const int64_t started = now_us();
            unsigned reported = 0;
            report(update_state::downloading, order.version, 0, update_error::none);
            while (k->failure == update_error::none && k->total < order.size) {
                const ssize_t n = recv(fd, buf, sizeof buf, 0);
                if (n > 0) {
                    if (!http->feed(buf, static_cast<size_t>(n), on_body, k)) {
                        k->failure = update_error::download;
                        break;
                    }
                    if (http->state() == caption_http::phase::body && http->status() != 200) {
                        ESP_LOGW(TAG, "update: Relay answered the image with %d", http->status());
                        k->failure = update_error::download;
                        break;
                    }
                    const unsigned pct = static_cast<unsigned>((uint64_t{k->total} * 100) / order.size);
                    if (pct >= reported + update_format::kProgressStepPercent && pct < 100) {
                        reported = pct - pct % update_format::kProgressStepPercent;
                        report(update_state::downloading, order.version, reported, update_error::none);
                    }
                    continue;
                }
                if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                    if (now_us() - started > update_format::kMaxDownloadSeconds * kSecondUs) {
                        k->failure = update_error::download;
                        break;
                    }
                    continue;
                }
                break;  // closed, or an error
            }
            shutdown(fd, SHUT_RDWR);
            close(fd);
            if (k->failure == update_error::none && http->status() != 200) {
                ESP_LOGW(TAG, "update: Relay answered the image with %d", http->status());
                k->failure = update_error::download;
            }
            if (k->failure == update_error::none && k->total != order.size) {
                ESP_LOGW(TAG, "update: the image ended at %u of %u bytes", static_cast<unsigned>(k->total),
                         static_cast<unsigned>(order.size));
                k->failure = update_error::download;
            }
            if (k->failure == update_error::none) {
                flush(*k, k->stage, k->stage_len);
            }
            if (k->failure == update_error::none) {
                uint8_t digest[32];
                mbedtls_sha256_finish(&k->sha, digest);
                char hex[65];
                static const char kDigits[] = "0123456789abcdef";
                for (size_t i = 0; i < sizeof digest; ++i) {
                    hex[2 * i] = kDigits[digest[i] >> 4];
                    hex[2 * i + 1] = kDigits[digest[i] & 0x0F];
                }
                hex[64] = '\0';
                if (std::strcmp(hex, order.sha256) != 0) {
                    ESP_LOGW(TAG, "update: the image's checksum is not the one Relay named");
                    k->failure = update_error::download;
                }
            }
            result = k->failure;
            if (result != update_error::none) {
                if (k->begun) {
                    esp_ota_abort(k->handle);
                }
            } else if (esp_ota_end(k->handle) != ESP_OK) {
                // **The signature, checked by the call that closes the slot.** An image not signed with the
                // key this firmware trusts is refused here, and the slot is never made bootable.
                ESP_LOGW(TAG, "update: refused (signature)");
                result = update_error::signature;
            }
        }
    }
    mbedtls_sha256_free(&k->sha);
    const esp_partition_t *target = k->target;
    std::free(k);
    delete http;
    if (result != update_error::none) {
        return result;
    }
    Guard guard;
    if (!s_.order.present || std::strcmp(s_.order.version, order.version) != 0) {
        ESP_LOGI(TAG, "update: the order changed while %s was fetched; it is not kept", order.version);
        return update_error::none;
    }
    s_.staged = true;
    s_.staged_part = target;
    copy_text(s_.staged_version, sizeof s_.staged_version, order.version);
    return update_error::none;
}

// The bootloader is told about the slot only here, when the panel is about to restart. A power cut before this
// line boots the image the panel was already running, which is the point of waiting.
void install_and_restart(const esp_partition_t *part, const char *version)
{
    // The attempt is on record before the bootloader is pointed at the slot, so a restart is never a boot nobody
    // remembers asking for. If either step fails the panel does not restart, the install is reported failed, and an
    // attempt that was written is taken back out.
    update_record_t record = {};
    settings_load_update(&record);
    std::snprintf(record.attempt, sizeof record.attempt, "%s", version);
    const esp_err_t saved = settings_save_update(record);
    const esp_err_t pointed = saved == ESP_OK ? esp_ota_set_boot_partition(part) : ESP_FAIL;
    if (saved != ESP_OK || pointed != ESP_OK) {
        if (saved == ESP_OK) {
            record.attempt[0] = '\0';
            settings_save_update(record);
        }
        {
            Guard guard;
            s_.staged = false;
            s_.staged_part = nullptr;
            s_.state = update_state::failed;
            s_.error = update_error::install;
        }
        ESP_LOGW(TAG, "update: the bootloader would not take the image (install)");
        report(update_state::failed, version, 0, update_error::install);
        return;
    }
    {
        Guard guard;
        s_.state = update_state::restarting;
    }
    report(update_state::restarting, version, 100, update_error::none);
    ESP_LOGI(TAG, "update: restarting into %s", version);
    vTaskDelay(pdMS_TO_TICKS(500));
    esp_restart();
}

void tick()
{
    const int64_t now = now_us();

    // The proof bound. A new image that has not reached Relay by now is taken back by the bootloader, which
    // does it on the next boot because the image is still pending. The restart is the way to ask.
    bool overdue = false;
    {
        Guard guard;
        overdue = s_.pending && now - s_.boot_us > static_cast<int64_t>(update_format::kConfirmSeconds) * kSecondUs;
    }
    if (overdue) {
        ESP_LOGW(TAG, "update: not confirmed within %u s; restarting, and the bootloader takes the image back",
                 static_cast<unsigned>(update_format::kConfirmSeconds));
        vTaskDelay(pdMS_TO_TICKS(500));
        esp_restart();
    }

    report_outcome_if_any();

    bool staged = false;
    bool operator_now = false;
    uint32_t since = 0;
    const esp_partition_t *part = nullptr;
    char version[update_format::kVersionBytes] = "";
    {
        Guard guard;
        staged = s_.staged;
        operator_now = s_.order.now;
        since = static_cast<uint32_t>((now - s_.line_us) / kSecondUs);
        part = s_.staged_part;
        std::strcpy(version, s_.staged_version);
    }
    if (update_format::may_restart(staged, operator_now, update_format::quiet_moment(since))) {
        install_and_restart(part, version);
        return;
    }

    bool due = false;
    update_format::Order order = {};
    {
        Guard guard;
        order = s_.order;
        const bool already_staged = s_.staged && std::strcmp(s_.staged_version, order.version) == 0;
        // Not while a new image is on approval (it must prove itself first), not for a version the bootloader
        // has just taken back and Relay has not yet heard about, and not before the retry wait is over.
        due = order.present && !s_.pending && !already_staged && now >= s_.retry_at_us &&
              !(s_.rolled_back[0] != '\0' && std::strcmp(s_.rolled_back, order.version) == 0);
        if (due) {
            s_.staged = false;
            s_.staged_part = nullptr;
            s_.state = update_state::downloading;
            s_.error = update_error::none;
        }
    }
    if (!due) {
        return;
    }

    ESP_LOGI(TAG, "update: fetching %s (%u bytes)", order.version, static_cast<unsigned>(order.size));
    const update_error result = download(order);
    if (result == update_error::none) {
        bool kept = false;
        {
            Guard guard;
            s_.failures = 0;
            s_.retry_at_us = 0;
            kept = s_.staged;
            s_.state = kept ? update_state::ready : update_state::idle;
        }
        if (kept) {
            ESP_LOGI(TAG, "update: %s verified; it restarts at a quiet moment or on the operator's word", order.version);
            report(update_state::ready, order.version, 100, update_error::none);
        }
        return;
    }
    uint32_t wait = 0;
    {
        Guard guard;
        s_.failures += 1;
        s_.state = update_state::failed;
        s_.error = result;
        wait = update_format::retry_seconds(s_.failures);
        s_.retry_at_us = now + static_cast<int64_t>(wait) * kSecondUs;
    }
    ESP_LOGW(TAG, "update: failed (%s); not trying again for %u s", update_format::error_word(result),
             static_cast<unsigned>(wait));
    report(update_state::failed, order.version, 0, result);
}

void task(void *)
{
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        tick();
    }
}

}  // namespace

esp_err_t firmware_update_start(bool settings_readable)
{
    if (s_lock == nullptr) {
        s_lock = xSemaphoreCreateMutex();
        if (s_lock == nullptr) {
            return ESP_ERR_NO_MEM;
        }
    }
    relay_settings_t settings = {};
    settings_load(&settings);
    update_record_t record = {};
    settings_load_update(&record);
    {
        Guard guard;
        std::snprintf(s_.panel_id, sizeof s_.panel_id, "%s", settings.panel_id);
        s_.settings_readable = settings_readable;
        s_.boot_us = now_us();
        s_.line_us = s_.boot_us;
        const esp_partition_t *running = esp_ota_get_running_partition();
        esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
        if (running != nullptr && esp_ota_get_state_partition(running, &state) == ESP_OK &&
            state == ESP_OTA_IMG_PENDING_VERIFY) {
            s_.pending = true;
        }
    }
    // What the last boot was, from the record the restart left. The bootloader's own `esp_ota_get_last_invalid_
    // partition` is not used: the record needs no image validation to answer, and it says what happened.
    if (s_.pending) {
        // On approval: the attempt stays on record until this image reaches Relay (`firmware_update_reached_relay`).
        ESP_LOGI(TAG, "update: %s is on approval; it must reach Relay within %u s", RELAY_FW_VERSION,
                 static_cast<unsigned>(update_format::kConfirmSeconds));
    } else if (update_format::attempt_was_taken_back(record.attempt, RELAY_FW_VERSION, false)) {
        std::snprintf(record.pending, sizeof record.pending, "rolled_back %s", record.attempt);
        {
            Guard guard;
            copy_text(s_.rolled_back, sizeof s_.rolled_back, record.attempt);
        }
        ESP_LOGW(TAG, "update: the bootloader took %s back at the last boot; it is not fetched again until Relay has heard",
                 record.attempt);
        record.attempt[0] = '\0';
        settings_save_update(record);
    } else if (record.attempt[0] != '\0') {
        // The attempt is the image this boot runs and it is not on approval: it was confirmed and the record was
        // not cleared. Clear it now, so it is not read as a rollback later.
        record.attempt[0] = '\0';
        settings_save_update(record);
    }
    if (xTaskCreate(task, "update", kStackBytes, nullptr, 2, nullptr) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void firmware_update_offer(const update_format::Order &order)
{
    if (s_lock == nullptr) {
        return;
    }
    Guard guard;
    if (!order.present) {
        if (s_.order.present) {
            ESP_LOGI(TAG, "update: the order was withdrawn");
        }
        s_.order = update_format::Order{};
        s_.staged = false;
        s_.staged_part = nullptr;
        if (s_.state == update_state::ready || s_.state == update_state::failed) {
            s_.state = update_state::idle;
            s_.error = update_error::none;
        }
        return;
    }
    const bool new_version = !s_.order.present || std::strcmp(s_.order.version, order.version) != 0;
    s_.order = order;
    if (new_version) {
        // A different version is a different question: the retry wait belongs to the old one, and a verified image
        // for the old one is no longer what would boot.
        s_.failures = 0;
        s_.retry_at_us = 0;
        if (s_.staged && std::strcmp(s_.staged_version, order.version) != 0) {
            s_.staged = false;
            s_.staged_part = nullptr;
            s_.state = update_state::idle;
        }
    }
}

void firmware_update_reached_relay()
{
    if (s_lock == nullptr) {
        return;
    }
    bool pending = false;
    bool settings_readable = false;
    {
        Guard guard;
        pending = s_.pending;
        settings_readable = s_.settings_readable;
    }
    if (!ota_confirmed_by(pending, settings_readable, true)) {
        return;
    }
    if (esp_ota_mark_app_valid_cancel_rollback() != ESP_OK) {
        ESP_LOGW(TAG, "update: could not confirm %s; the bootloader will take it back", RELAY_FW_VERSION);
        return;
    }
    {
        Guard guard;
        s_.pending = false;
    }
    // The attempt is over. It is cleared so the next boot is not read as a rollback, and the outcome is left for
    // the task to tell Relay until Relay answers.
    update_record_t record = {};
    settings_load_update(&record);
    record.attempt[0] = '\0';
    std::snprintf(record.pending, sizeof record.pending, "installed %s", RELAY_FW_VERSION);
    settings_save_update(record);
    ESP_LOGI(TAG, "update: confirmed %s", RELAY_FW_VERSION);
}

void firmware_update_note_line()
{
    if (s_lock == nullptr) {
        return;
    }
    Guard guard;
    s_.line_us = now_us();
}
