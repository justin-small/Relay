// The LIVE buttons' task and socket. `service_control.h` says what it is for.

#include "service_control.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <new>

#include "captions.h"
#include "config.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "feeds.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "panel_link.h"

namespace {

const char *TAG = "control";

struct Pending {
    bool waiting = false;
    service_action action = service_action::next;
    char type[kServiceTypeBytes] = {};
};

portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
Pending s_pending;
service_control_state s_state;
TaskHandle_t s_task = nullptr;
char s_id[33] = {};

// The task's own.
char s_request[512];
char s_body[512];

uint32_t now_ms()
{
    return static_cast<uint32_t>(esp_timer_get_time() / 1000);
}

void set_result(service_result r, service_action a)
{
    taskENTER_CRITICAL(&s_mux);
    s_state.result = r;
    s_state.action = a;
    s_state.at_ms = now_ms();
    ++s_state.revision;
    if (r != service_result::sending) {
        s_pending.waiting = false;
    }
    taskEXIT_CRITICAL(&s_mux);
}

struct Body {
    char *out;
    size_t cap;
    size_t len;
};

void on_body(void *ctx, const char *p, size_t n)
{
    auto *b = static_cast<Body *>(ctx);
    if (b->len + 1 >= b->cap) {
        return;   // the rest is the service state, which the feed brings anyway
    }
    if (b->len + n + 1 > b->cap) {
        n = b->cap - b->len - 1;
    }
    memcpy(b->out + b->len, p, n);
    b->len += n;
    b->out[b->len] = '\0';
}

// One POST. Returns Relay's status (0: no answer) with the start of the body in `s_body`.
int send_press(service_action action, const char *type, const char *key)
{
    char url[sizeof(relay_settings_t::relay_addr)];
    char host[sizeof(relay_settings_t::relay_addr)];
    uint16_t port = 0;
    if (!feeds_relay_url(url, sizeof url) || !captions_has_address() ||
        !caption_url_split(url, host, sizeof host, &port)) {
        return 0;
    }
    const size_t n = service_action_request(s_request, sizeof s_request, host, port, s_id, action, key, type,
                                            RELAY_FW_VERSION);
    if (n == 0) {
        ESP_LOGE(TAG, "control: the request does not fit");
        return 0;
    }
    const int fd = captions_connect(host, port);
    if (fd < 0) {
        return 0;
    }
    auto *http = new (std::nothrow) caption_http();
    Body body = {s_body, sizeof s_body, 0};
    s_body[0] = '\0';
    int status = 0;
    if (http != nullptr && captions_send(fd, s_request, n)) {
        char buf[512];
        // Relay waits for Planning Center (a few seconds at worst) before it answers.
        const int64_t deadline = esp_timer_get_time() + 15 * 1000 * 1000;
        while (esp_timer_get_time() < deadline) {
            const ssize_t got = recv(fd, buf, sizeof buf, 0);
            if (got > 0) {
                if (!http->feed(buf, static_cast<size_t>(got), on_body, &body) ||
                    http->state() == caption_http::phase::done) {
                    break;
                }
                continue;
            }
            if (got < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                continue;
            }
            break;   // closed: the body is whole (Connection: close)
        }
        status = http->status();
    }
    delete http;
    shutdown(fd, SHUT_RDWR);
    close(fd);
    return status;
}

void task(void *)
{
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        taskENTER_CRITICAL(&s_mux);
        const Pending p = s_pending;
        taskEXIT_CRITICAL(&s_mux);
        if (!p.waiting) {
            continue;
        }
        const panel_service svc = panel_link_service();
        if (!service_mode_controls(svc.mode) || svc.key[0] == '\0') {
            set_result(service_result::not_allowed, p.action);
            continue;
        }
        const int status = send_press(p.action, p.type, svc.key);
        const service_result r = service_result_from(status, s_body, strlen(s_body));
        ESP_LOGI(TAG, "control: %s on service type %s: %d, %s", service_action_path(p.action), p.type, status,
                 r == service_result::done ? "done" : service_result_words(r));
        set_result(r, p.action);
    }
}

}  // namespace

esp_err_t service_control_start(const relay_settings_t &settings)
{
    if (s_task != nullptr) {
        return ESP_OK;
    }
    if (!settings_panel_id_valid(settings.panel_id)) {
        return ESP_ERR_INVALID_STATE;
    }
    snprintf(s_id, sizeof s_id, "%s", settings.panel_id);
    if (xTaskCreatePinnedToCore(task, "control", 4096, nullptr, 3, &s_task, 0) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

bool service_control_press(service_action action, const char *type)
{
    if (s_task == nullptr || type == nullptr || !service_id_ok(type)) {
        return false;
    }
    taskENTER_CRITICAL(&s_mux);
    const bool busy = s_pending.waiting;
    if (!busy) {
        s_pending.waiting = true;
        s_pending.action = action;
        snprintf(s_pending.type, sizeof s_pending.type, "%s", type);
        s_state.result = service_result::sending;
        s_state.action = action;
        s_state.at_ms = now_ms();
        ++s_state.revision;
    }
    taskEXIT_CRITICAL(&s_mux);
    if (busy) {
        ESP_LOGI(TAG, "control: %s ignored, the last press is still waiting", service_action_path(action));
        return false;
    }
    ESP_LOGI(TAG, "control: %s pressed", service_action_path(action));
    xTaskNotifyGive(s_task);
    return true;
}

service_control_state service_control_get()
{
    taskENTER_CRITICAL(&s_mux);
    const service_control_state st = s_state;
    taskEXIT_CRITICAL(&s_mux);
    return st;
}
