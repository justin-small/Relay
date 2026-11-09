#pragma once

// A device's own setup network: its address, who may use it, and the
// DNS answer that turns a phone's captive-portal check into the setup page.
//
// Shared by the compact display's setup network (#1267, where it was written on
// 2026-10-28) and the I/O board's access point. Both run ESP-IDF's default
// access point, 192.168.4.1/24, whose DHCP server names the device itself as
// the phone's resolver. What the device does with a captive check once the name
// resolves differs per target, so that stays in the target: the compact display
// redirects to its plain-HTTP page, the I/O board to its HTTPS one.

#include <cstddef>
#include <cstdint>
#include <cstring>

constexpr uint32_t kSetupHostAddress = 0xC0A80401;  // 192.168.4.1
constexpr uint32_t kSetupSubnetMask = 0xFFFFFF00;   // 192.168.4.0/24

// Whether a request from `peer` (host byte order) came from a client of the setup
// network. The setup page listens on every interface, because the station may be on
// the building's network during recovery, and it must answer only the access point's
// clients. Those are .2 to .254 of 192.168.4.0/24.
inline bool setup_peer_allowed(uint32_t peer)
{
    if ((peer & kSetupSubnetMask) != (kSetupHostAddress & kSetupSubnetMask)) {
        return false;
    }
    const uint32_t host = peer & 0xFF;
    return host >= 2 && host <= 254;
}

// **The setup network answers DNS, and every name is the panel** (2026-10-28). A phone that joins sends DNS to
// the address its lease names as the resolver, which is the panel. With nothing listening there, each query
// drew an ICMP port unreachable, and an iPhone then held the page for 46 seconds before it sent the SYN: its
// captive-portal check (`captive.apple.com`) and a content filter's own lookups waited on names that never
// resolved. Answering every A query with the panel's address makes those lookups end at once, and it makes the
// captive-portal check reach the setup page, so a phone opens it by itself after it joins. The setup network
// leads nowhere else, so no answer here can send a phone to a wrong host.
//
// The reply to `query` (the `n` bytes of one UDP payload) is written to `out` and its length returned; 0 means
// no reply is sent. A query that is not a standard query with one question, or that does not parse, gets
// none. A question for A in class IN gets the panel's `address` (host byte order) with a ten-second TTL, short
// so a phone that leaves the setup network does not keep it. Any other type (AAAA, HTTPS) gets an answer with
// no records, which says the name exists and has nothing of that type, so a phone falls back to A at once.
inline size_t setup_dns_answer(const uint8_t *query, size_t n, uint32_t address, uint8_t *out, size_t cap)
{
    constexpr size_t kHeader = 12;
    if (n < kHeader || n > 512) {
        return 0;
    }
    const uint8_t flags = query[2];
    if ((flags & 0x80) != 0 || ((flags >> 3) & 0x0F) != 0) {
        return 0;  // a response, or an opcode other than a standard query
    }
    if (query[4] != 0 || query[5] != 1) {
        return 0;  // exactly one question
    }
    size_t i = kHeader;
    for (;;) {
        if (i >= n) {
            return 0;
        }
        const uint8_t label = query[i];
        if (label == 0) {
            ++i;
            break;
        }
        if (label > 63) {
            return 0;  // a question's name is never compressed
        }
        i += 1 + label;
    }
    if (i + 4 > n) {
        return 0;
    }
    const uint16_t qtype = static_cast<uint16_t>((query[i] << 8) | query[i + 1]);
    const uint16_t qclass = static_cast<uint16_t>((query[i + 2] << 8) | query[i + 3]);
    const size_t question_end = i + 4;
    const bool answer_a = qtype == 1 && qclass == 1;
    const size_t len = question_end + (answer_a ? 16 : 0);
    if (len > cap) {
        return 0;
    }
    // The header and the question, as asked. Anything after the question (an EDNS record) is not echoed.
    std::memcpy(out, query, question_end);
    out[2] = static_cast<uint8_t>(0x80 | 0x04 | (flags & 0x01));  // a response, authoritative, RD as asked
    out[3] = 0x80;                                                 // recursion available, no error
    out[6] = 0;
    out[7] = answer_a ? 1 : 0;  // answers
    out[8] = out[9] = 0;        // authority
    out[10] = out[11] = 0;      // additional
    if (answer_a) {
        uint8_t *a = out + question_end;
        const uint8_t record[16] = {0xC0, 0x0C,             // the name: a pointer to the question's
                                    0x00, 0x01, 0x00, 0x01, // A, IN
                                    0x00, 0x00, 0x00, 0x0A, // TTL 10 s
                                    0x00, 0x04,
                                    static_cast<uint8_t>(address >> 24), static_cast<uint8_t>(address >> 16),
                                    static_cast<uint8_t>(address >> 8), static_cast<uint8_t>(address)};
        std::memcpy(a, record, sizeof record);
    }
    return len;
}

