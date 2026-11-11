// Alerts' client: its task and socket. `alert_client.h` says what it is for; the requests are `alert_format.h`'s.

#include "alert_client.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <new>

#include "alert_format.h"
#include "captions.h"
#include "config.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "feeds.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"

namespace {

const char *TAG = "alerts";

portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
char s_send[kAlertIdBytes] = {};      // a button tapped and not yet sent
char s_ack[kAlertFlashIdBytes] = {};  // an alert tapped away and not yet acknowledged
alert_client_state s_state;
TaskHandle_t s_task = nullptr;
char s_id[33] = {};
char s_request[512];

uint32_t now_ms()
{
    return static_cast<uint32_t>(esp_timer_get_time() / 1000);
}

void set_result(alert_send_result r)
{
    taskENTER_CRITICAL(&s_mux);
    s_state.result = r;
    s_state.at_ms = now_ms();
    ++s_state.revision;
    taskEXIT_CRITICAL(&s_mux);
}

// One POST of `s_request` (`n` bytes). Returns Relay's status, 0 for no answer.
int post(size_t n, const char *host, uint16_t port)
{
    const int fd = captions_connect(host, port);
    if (fd < 0) {
        return 0;
    }
    auto *http = new (std::nothrow) caption_http();
    int status = 0;
    if (http != nullptr && captions_send(fd, s_request, n)) {
        char buf[256];
        const int64_t deadline = esp_timer_get_time() + 8 * 1000 * 1000;
        while (esp_timer_get_time() < deadline) {
            const ssize_t got = recv(fd, buf, sizeof buf, 0);
            if (got > 0) {
                // The body is the new state, which the Alert feed brings anyway: only the status is read.
                if (!http->feed(buf, static_cast<size_t>(got), [](void *, const char *, size_t) {}, nullptr) ||
                    http->status() != 0) {
                    break;
                }
                continue;
            }
            if (got < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
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

bool relay(char *host, size_t cap, uint16_t *port)
{
    char url[sizeof(relay_settings_t::relay_addr)];
    return feeds_relay_url(url, sizeof url) && captions_has_address() && caption_url_split(url, host, cap, port);
}

void task(void *)
{
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        for (;;) {
            char send[kAlertIdBytes];
            char ack[kAlertFlashIdBytes];
            taskENTER_CRITICAL(&s_mux);
            memcpy(send, s_send, sizeof send);
            memcpy(ack, s_ack, sizeof ack);
            s_send[0] = '\0';
            s_ack[0] = '\0';
            taskEXIT_CRITICAL(&s_mux);
            if (send[0] == '\0' && ack[0] == '\0') {
                break;
            }
            char host[sizeof(relay_settings_t::relay_addr)];
            uint16_t port = 0;
            const bool known = relay(host, sizeof host, &port);
            // The acknowledgement first: it stops someone's flashing screen.
            if (ack[0] != '\0') {
                const size_t n = known ? alert_ack_request(s_request, sizeof s_request, host, port, ack, s_id,
                                                           RELAY_FW_VERSION)
                                       : 0;
                const int status = n > 0 ? post(n, host, port) : 0;
                ESP_LOGI(TAG, "alerts: acknowledged %s: %d", ack, status);
            }
            if (send[0] != '\0') {
                const size_t n = known ? alert_send_request(s_request, sizeof s_request, host, port, send, s_id,
                                                            RELAY_FW_VERSION)
                                       : 0;
                const int status = n > 0 ? post(n, host, port) : 0;
                ESP_LOGI(TAG, "alerts: sent button %s: %d", send, status);
                set_result(status == 200   ? alert_send_result::sent
                           : status == 0   ? alert_send_result::unreachable
                                           : alert_send_result::refused);
            }
        }
    }
}

}  // namespace

esp_err_t alert_client_start(const relay_settings_t &settings)
{
    if (s_task != nullptr) {
        return ESP_OK;
    }
    if (!settings_panel_id_valid(settings.panel_id)) {
        return ESP_ERR_INVALID_STATE;
    }
    snprintf(s_id, sizeof s_id, "%s", settings.panel_id);
    if (xTaskCreatePinnedToCore(task, "alerts", 4096, nullptr, 3, &s_task, 0) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

bool alert_client_send(const char *button)
{
    if (s_task == nullptr || button == nullptr || !alert_hex_ok(button, kAlertIdBytes - 1)) {
        return false;
    }
    taskENTER_CRITICAL(&s_mux);
    memcpy(s_send, button, kAlertIdBytes);
    s_state.result = alert_send_result::sending;
    s_state.at_ms = now_ms();
    ++s_state.revision;
    taskEXIT_CRITICAL(&s_mux);
    ESP_LOGI(TAG, "alerts: button %s pressed", button);
    xTaskNotifyGive(s_task);
    return true;
}

bool alert_client_ack(const char *alert_id)
{
    if (s_task == nullptr || alert_id == nullptr || !alert_hex_ok(alert_id, kAlertFlashIdBytes - 1)) {
        return false;
    }
    taskENTER_CRITICAL(&s_mux);
    memcpy(s_ack, alert_id, kAlertFlashIdBytes);
    taskEXIT_CRITICAL(&s_mux);
    xTaskNotifyGive(s_task);
    return true;
}

alert_client_state alert_client_get()
{
    taskENTER_CRITICAL(&s_mux);
    const alert_client_state st = s_state;
    taskEXIT_CRITICAL(&s_mux);
    return st;
}
