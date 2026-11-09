#include "diag.h"

#if CONFIG_RELAY_DEV_DIAG

#include <string.h>

#include "display.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/opt.h"
#include "lwip/pbuf.h"
#include "lwip/raw.h"
#include "lwip/stats.h"
#include "lwip/tcpip.h"

namespace {

constexpr char TAG[] = "diag";
constexpr uint32_t kIntervalMs = 5000;

// **What the five-second block does, switched from the serial console (`diag <mode>`).** The panel flickered on
// every block, with the heap walk already gone, so the block is split into its parts to find which one the
// panel feels: the printing, the task snapshot or the Wi-Fi queries. Each mode keeps the five-second rhythm, so the
// person watching the panel looks for the same beat.
enum class Mode { on, off, quiet, print, tasks, tasks0, tasks1, wifi };
volatile Mode s_mode = Mode::on;

// One snapshot task pinned to each core, for `tasks0` and `tasks1`: the snapshot's critical section masks only the
// core it runs on, so which core it runs on says whether the panel's interrupt shares it.
TaskHandle_t s_snap[2] = {};

void snap_task(void *)
{
    static TaskStatus_t scratch[2][32];
    const int core = xPortGetCoreID();
    for (;;) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        uint32_t total = 0;
        uxTaskGetSystemState(scratch[core], 32, &total);
    }
}

// LVGL flushes since the last line: how many, their summed time and the slowest. Written by the
// LVGL task and read by the diag task; a torn read costs one wrong figure in a log line.
volatile uint32_t s_flushes = 0;
volatile int64_t s_flush_us = 0;
volatile int64_t s_flush_max_us = 0;

// **The packet trace.** Every IPv4 TCP and UDP packet that reaches the panel, as a raw PCB sees it before
// the stack hands it on: who sent it, to which port, the TCP flags, and for DNS the name asked for. A slow
// setup page is slow either at the panel or before the phone ever sends a SYN, and only a per-packet
// timeline tells the two apart. The callback runs in lwIP's thread, so it only copies into a ring; the
// diag task prints. A full ring counts what it lost rather than blocking the network.
struct Packet {
    int64_t at_us;
    uint32_t src;  // host order
    uint32_t dst;
    uint16_t sport;
    uint16_t dport;
    uint16_t len;
    uint8_t proto;
    uint8_t tcp_flags;
    char qname[40];  // DNS only
};
constexpr int kRing = 96;
Packet s_ring[kRing];
volatile uint32_t s_head = 0;  // written by lwIP's thread
uint32_t s_tail = 0;           // read by the diag task
volatile uint32_t s_lost = 0;

uint32_t read_be32(const uint8_t *b)
{
    return (uint32_t{b[0]} << 24) | (uint32_t{b[1]} << 16) | (uint32_t{b[2]} << 8) | b[3];
}

// A DNS question's name as dotted text, from the labels after the 12-byte header. Truncated, never overrun.
void dns_name(const uint8_t *dns, int len, char *out, int out_size)
{
    int i = 12;
    int o = 0;
    while (i < len && dns[i] != 0 && o < out_size - 1) {
        const int label = dns[i++];
        if (label > 63) {
            break;  // a compression pointer cannot open a question
        }
        if (o > 0 && o < out_size - 1) {
            out[o++] = '.';
        }
        for (int k = 0; k < label && i < len && o < out_size - 1; ++k) {
            const uint8_t c = dns[i++];
            out[o++] = (c >= 0x21 && c < 0x7F) ? static_cast<char>(c) : '?';
        }
    }
    out[o] = '\0';
}

u8_t on_packet(void *, struct raw_pcb *, struct pbuf *p, const ip_addr_t *)
{
    uint8_t hdr[60 + 20 + 12 + 40];
    const int got = pbuf_copy_partial(p, hdr, sizeof hdr, 0);
    if (got < 20 || (hdr[0] >> 4) != 4) {
        return 0;
    }
    const int ihl = (hdr[0] & 0x0F) * 4;
    const uint32_t head = s_head;
    if (head - s_tail >= kRing) {
        s_lost = s_lost + 1;
        return 0;
    }
    Packet &k = s_ring[head % kRing];
    k = {};
    k.at_us = esp_timer_get_time();
    k.proto = hdr[9];
    k.len = static_cast<uint16_t>(p->tot_len);
    k.src = read_be32(hdr + 12);
    k.dst = read_be32(hdr + 16);
    if (got >= ihl + 4) {
        k.sport = static_cast<uint16_t>((hdr[ihl] << 8) | hdr[ihl + 1]);
        k.dport = static_cast<uint16_t>((hdr[ihl + 2] << 8) | hdr[ihl + 3]);
    }
    if (k.proto == 6 && got >= ihl + 14) {
        k.tcp_flags = hdr[ihl + 13];
    }
    if (k.proto == 17 && (k.dport == 53 || k.dport == 5353) && got > ihl + 8 + 12) {
        dns_name(hdr + ihl + 8, got - ihl - 8, k.qname, sizeof k.qname);
    }
    s_head = head + 1;
    return 0;  // never eat it: the stack goes on as if the trace were not there
}

void start_trace(void *)
{
    static const u8_t kProtos[] = {6, 17};  // TCP and UDP
    for (const u8_t proto : kProtos) {
        raw_pcb *pcb = raw_new_ip_type(IPADDR_TYPE_V4, proto);
        if (pcb != nullptr) {
            raw_recv(pcb, on_packet, nullptr);
        }
    }
}

void print_ip(char *out, size_t size, uint32_t a)
{
    snprintf(out, size, "%u.%u.%u.%u", static_cast<unsigned>(a >> 24), static_cast<unsigned>((a >> 16) & 0xFF),
             static_cast<unsigned>((a >> 8) & 0xFF), static_cast<unsigned>(a & 0xFF));
}

void log_trace()
{
    const uint32_t head = s_head;
    while (s_tail != head) {
        const Packet &k = s_ring[s_tail % kRing];
        char src[16];
        char dst[16];
        print_ip(src, sizeof src, k.src);
        print_ip(dst, sizeof dst, k.dst);
        if (k.proto == 6) {
            const uint8_t f = k.tcp_flags;
            ESP_LOGI(TAG, "pkt %lld ms tcp %s:%u > %s:%u %s%s%s%s%s len %u", static_cast<long long>(k.at_us / 1000),
                     src, k.sport, dst, k.dport, (f & 0x02) ? "SYN " : "", (f & 0x10) ? "ACK " : "",
                     (f & 0x08) ? "PSH " : "", (f & 0x01) ? "FIN " : "", (f & 0x04) ? "RST " : "", k.len);
        } else {
            ESP_LOGI(TAG, "pkt %lld ms udp %s:%u > %s:%u len %u%s%s", static_cast<long long>(k.at_us / 1000), src,
                     k.sport, dst, k.dport, k.len, k.qname[0] ? " dns " : "", k.qname);
        }
        s_tail = s_tail + 1;
    }
    if (s_lost != 0) {
        ESP_LOGW(TAG, "pkt trace lost %u packet(s): the ring was full", static_cast<unsigned>(s_lost));
        s_lost = 0;
    }
}

const char *reset_name(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON: return "power-on";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "interrupt watchdog";
    case ESP_RST_TASK_WDT: return "task watchdog";
    case ESP_RST_WDT: return "watchdog";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_DEEPSLEEP: return "deep sleep";
    default: return "other";
    }
}

void log_cpu(TaskStatus_t *before, UBaseType_t n_before, TaskStatus_t *now, UBaseType_t n, uint32_t span)
{
    for (UBaseType_t i = 0; i < n; ++i) {
        uint32_t prev = 0;
        for (UBaseType_t j = 0; j < n_before; ++j) {
            if (before[j].xTaskNumber == now[i].xTaskNumber) {
                prev = before[j].ulRunTimeCounter;
            }
        }
        const uint32_t used = now[i].ulRunTimeCounter - prev;
        // The counter covers both cores, so a fully busy chip adds to 200.
        const unsigned permille = span ? static_cast<unsigned>((uint64_t{used} * 1000) / span) : 0;
        if (permille >= 10) {
            ESP_LOGI(TAG, "  cpu %3u.%u%% %-14s core %d prio %2u stack-left %u", permille / 10, permille % 10,
                     now[i].pcTaskName, now[i].xCoreID == tskNO_AFFINITY ? -1 : static_cast<int>(now[i].xCoreID),
                     static_cast<unsigned>(now[i].uxCurrentPriority), static_cast<unsigned>(now[i].usStackHighWaterMark));
        }
    }
}

void log_wifi()
{
    wifi_sta_list_t list = {};
    if (esp_wifi_ap_get_sta_list(&list) == ESP_OK) {
        ESP_LOGI(TAG, "wifi ap: %d client(s)", list.num);
        for (int i = 0; i < list.num; ++i) {
            const uint8_t *m = list.sta[i].mac;
            ESP_LOGI(TAG, "  client %02x:%02x:%02x:%02x:%02x:%02x rssi %d dBm", m[0], m[1], m[2], m[3], m[4], m[5],
                     list.sta[i].rssi);
        }
    }
    wifi_ap_record_t ap = {};
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        ESP_LOGI(TAG, "wifi sta: '%s' rssi %d dBm channel %d", reinterpret_cast<const char *>(ap.ssid), ap.rssi,
                 ap.primary);
    }
    wifi_ps_type_t ps = WIFI_PS_NONE;
    if (esp_wifi_get_ps(&ps) == ESP_OK) {
        ESP_LOGI(TAG, "wifi power save mode %d (0 none, 1 min modem, 2 max modem)", static_cast<int>(ps));
    }
}

void log_lwip()
{
#if LWIP_STATS
    ESP_LOGI(TAG, "lwip link xmit %u recv %u drop %u err %u | ip drop %u | tcp xmit %u recv %u drop %u err %u",
             static_cast<unsigned>(lwip_stats.link.xmit), static_cast<unsigned>(lwip_stats.link.recv),
             static_cast<unsigned>(lwip_stats.link.drop), static_cast<unsigned>(lwip_stats.link.err),
             static_cast<unsigned>(lwip_stats.ip.drop), static_cast<unsigned>(lwip_stats.tcp.xmit),
             static_cast<unsigned>(lwip_stats.tcp.recv), static_cast<unsigned>(lwip_stats.tcp.drop),
             static_cast<unsigned>(lwip_stats.tcp.err));
    // The ICMP the panel sends is mostly "port unreachable", the answer to a UDP packet for a port nobody
    // listens on, so `udp proterr` beside it says how many were for a closed port (a phone asking DNS).
    ESP_LOGI(TAG, "lwip icmp recv %u xmit %u | udp recv %u xmit %u drop %u proterr %u",
             static_cast<unsigned>(lwip_stats.icmp.recv), static_cast<unsigned>(lwip_stats.icmp.xmit),
             static_cast<unsigned>(lwip_stats.udp.recv), static_cast<unsigned>(lwip_stats.udp.xmit),
             static_cast<unsigned>(lwip_stats.udp.drop), static_cast<unsigned>(lwip_stats.udp.proterr));
    ESP_LOGI(TAG, "lwip ip recv %u chkerr %u lenerr %u proterr %u opterr %u | tcp proterr %u rterr %u memerr %u",
             static_cast<unsigned>(lwip_stats.ip.recv), static_cast<unsigned>(lwip_stats.ip.chkerr),
             static_cast<unsigned>(lwip_stats.ip.lenerr), static_cast<unsigned>(lwip_stats.ip.proterr),
             static_cast<unsigned>(lwip_stats.ip.opterr), static_cast<unsigned>(lwip_stats.tcp.proterr),
             static_cast<unsigned>(lwip_stats.tcp.rterr), static_cast<unsigned>(lwip_stats.tcp.memerr));
#endif
}

// Every five seconds: what the board is doing, in one block, so a slow moment can be read against
// the frame rate, the CPU, the radio and the network stack at the same time.
void diag_task(void *)
{
    ESP_LOGI(TAG, "reset reason: %s; diagnostics every %u s", reset_name(esp_reset_reason()),
             static_cast<unsigned>(kIntervalMs / 1000));
    static TaskStatus_t before[32];
    static TaskStatus_t now[32];
    uint32_t total_before = 0;
    UBaseType_t n_before = uxTaskGetSystemState(before, 32, &total_before);
    uint32_t frames_before = display_frames_done();
    int64_t t_before = esp_timer_get_time();
    for (;;) {
        // The packet trace is printed each second, so its lines sit near the events they explain; the
        // summary block below waits for the whole interval.
        for (uint32_t waited = 0; waited < kIntervalMs; waited += 1000) {
            vTaskDelay(pdMS_TO_TICKS(1000));
            if (s_mode == Mode::on) {
                log_trace();
            }
        }
        const Mode mode = s_mode;
        if (mode == Mode::off) {
            continue;
        }
        if (mode == Mode::print) {
            // The full block's bytes with nothing measured: thirteen lines of about the same length.
            for (int i = 0; i < 13; ++i) {
                ESP_LOGI(TAG, "print test line %2d ............................................................", i);
            }
            continue;
        }
        if (mode == Mode::tasks0 || mode == Mode::tasks1) {
            xTaskNotifyGive(s_snap[mode == Mode::tasks0 ? 0 : 1]);
            continue;
        }
        if (mode == Mode::wifi) {
            wifi_sta_list_t list = {};
            wifi_ap_record_t ap = {};
            wifi_ps_type_t ps = WIFI_PS_NONE;
            esp_wifi_ap_get_sta_list(&list);
            esp_wifi_sta_get_ap_info(&ap);
            esp_wifi_get_ps(&ps);
            continue;
        }
        uint32_t total = 0;
        const UBaseType_t n = uxTaskGetSystemState(now, 32, &total);
        if (mode == Mode::tasks) {
            continue;
        }
        const int64_t t_now = esp_timer_get_time();
        const uint32_t frames = display_frames_done();
        const unsigned fps_x10 = static_cast<unsigned>((uint64_t{frames - frames_before} * 10000000ULL) /
                                                       static_cast<uint64_t>(t_now - t_before));
        const uint32_t flushes = s_flushes;
        const int64_t flush_us = s_flush_us;
        const int64_t flush_max = s_flush_max_us;
        s_flushes = 0;
        s_flush_us = 0;
        s_flush_max_us = 0;

        if (mode == Mode::quiet) {
            // Everything measured, wifi queried, nothing printed.
            wifi_ap_record_t ap = {};
            esp_wifi_sta_get_ap_info(&ap);
        } else {
        ESP_LOGI(TAG, "---- t=%lld s ----", static_cast<long long>(t_now / 1000000));
        ESP_LOGI(TAG, "panel %u.%u frames/s (target about 39)", fps_x10 / 10, fps_x10 % 10);
        ESP_LOGI(TAG, "lvgl %u flushes, %lld us total, %lld us slowest", static_cast<unsigned>(flushes),
                 static_cast<long long>(flush_us), static_cast<long long>(flush_max));
        // **No largest free block.** `heap_caps_get_largest_free_block` walks every block of the heap inside
        // the heap's lock, which is a critical section: interrupts are masked on this core for the whole walk,
        // and over an 8 MB PSRAM heap that is long enough for the RGB driver's bounce-buffer interrupt to miss
        // its refill. The panel flickered on every one of these blocks. Free and low-water sizes are
        // counters the allocator keeps, read without a walk.
        ESP_LOGI(TAG, "heap internal %u (low %u) dma %u psram %u",
                 static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)),
                 static_cast<unsigned>(heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL)),
                 static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_DMA)),
                 static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
        ESP_LOGI(TAG, "tasks %u", static_cast<unsigned>(n));
        log_cpu(before, n_before, now, n, total - total_before);
        log_wifi();
        log_lwip();
        }

        for (UBaseType_t i = 0; i < n; ++i) {
            before[i] = now[i];
        }
        n_before = n;
        total_before = total;
        frames_before = frames;
        t_before = t_now;
    }
}

}  // namespace

int64_t diag_ms_since(int64_t since_us)
{
    return (esp_timer_get_time() - since_us) / 1000;
}

void diag_note_flush(int64_t us)
{
    s_flushes = s_flushes + 1;
    s_flush_us = s_flush_us + us;
    if (us > s_flush_max_us) {
        s_flush_max_us = us;
    }
}

bool diag_set_mode(const char *name)
{
    static const struct {
        const char *name;
        Mode mode;
    } kModes[] = {{"on", Mode::on},       {"off", Mode::off},     {"quiet", Mode::quiet},
                  {"print", Mode::print}, {"tasks", Mode::tasks}, {"tasks0", Mode::tasks0}, {"tasks1", Mode::tasks1},
                  {"wifi", Mode::wifi}};
    for (const auto &m : kModes) {
        if (strcmp(name, m.name) == 0) {
            s_mode = m.mode;
            return true;
        }
    }
    return false;
}

// A failed allocation, logged where it happens: under a flood the low-water mark says how close internal RAM came
// to empty, and this says whether it got there and who asked.
static void IRAM_ATTR failed_alloc(size_t size, uint32_t caps, const char *function)
{
    esp_rom_printf("diag: alloc failed: %u bytes caps 0x%x in %s\n", static_cast<unsigned>(size),
                   static_cast<unsigned>(caps), function ? function : "?");
}

void diag_start()
{
    heap_caps_register_failed_alloc_callback(failed_alloc);
    tcpip_callback(start_trace, nullptr);
    xTaskCreatePinnedToCore(snap_task, "snap0", 2048, nullptr, 1, &s_snap[0], 0);
    xTaskCreatePinnedToCore(snap_task, "snap1", 2048, nullptr, 1, &s_snap[1], 1);
    // Core 0, away from the panel's interrupt on core 1 (`main.cpp`): the task snapshot below masks interrupts on its
    // own core while it walks the task lists, and on the panel's core that flickered the picture every block.
    xTaskCreatePinnedToCore(diag_task, "diag", 5120, nullptr, 1, nullptr, 0);
}

#endif  // CONFIG_RELAY_DEV_DIAG
