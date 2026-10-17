/*
 * SPDX-FileCopyrightText: 2026 Espressif Systems (Shanghai) CO LTD
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Modified 2026 by M. Kalantary for Ghost-Vision: multi-link sibling capture,
 * per-link PHY fingerprinting, per-link rate cap, UDP transport with discovery.
 */
/* Get recv router csi

   This example code is in the Public Domain (or CC0 licensed, at your option.)

   Unless required by applicable law or agreed to in writing, this
   software is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
   CONDITIONS OF ANY KIND, either express or implied.
*/

/* ==========================================================================
 * GHOST-VISION  --  wireless CSI node firmware
 * Based on Espressif's csi_recv_router example.
 *
 * IN PLAIN WORDS
 *   This program runs on each of the four small boards ("nodes"). It joins
 *   your WiFi, listens to the WiFi packets around it, measures how each one
 *   arrived (its "Channel State Information", or CSI), and sends those
 *   measurements to the console program on your PC. The board never decides
 *   anything itself -- it is a very fast, very careful ear.
 *
 * THE ONE LINE YOU CHANGE PER BOARD
 *   GV_NODE_ID, further down. Board 1 gets 1, board 2 gets 2, and so on.
 *   You also fill in your four boards' MAC addresses once (GV_KNOWN and
 *   GV_NODE_MAC). The step-by-step guide is in docs/GUIDE.md.
 *
 * WHAT WAS ADDED TO ESPRESSIF'S EXAMPLE (nothing was removed)
 *
 *   1. NODE IDENTITY.  GV_NODE_ID is compiled in and travels inside every
 *      packet. A node is N2 because its firmware says so -- not because of
 *      which USB socket or IP address it happens to have today.
 *
 *   2. LISTENING TO EVERY PATH.  Besides the router, each node also captures
 *      packets sent by the other three nodes, so four boards produce sixteen
 *      measurements instead of four. Each record is tagged with who sent it.
 *
 *   3. FRAME FINGERPRINT, PER PATH.  Only frames of the same physical format
 *      are comparable, so each path learns its most common format over its
 *      first GV_SIGLEN_LEARN records and then keeps only matching frames.
 *      The format key is the PHY mode and bandwidth (plus the rate, for
 *      legacy frames only). Frame length is deliberately NOT used -- an
 *      earlier version used it and was wrong (see docs/lessons-learned.md).
 *      A weak lock (below GV_LOCK_MIN_PCT agreement) is thrown away and
 *      learned again, up to GV_LOCK_TRIES times.
 *
 *   4. A SPEED LIMIT PER PATH.  No path may send more than GV_MAX_PER_SEC
 *      records per second. Loud paths are trimmed; quiet ones are untouched.
 *
 *   5. UDP STREAMING.  The CSI callback runs inside the WiFi driver task and
 *      must never block, so it only copies into a FreeRTOS queue. A separate
 *      task empties the queue and sends the packets.
 *
 *   6. FINDING THE CONSOLE.  The console announces itself on the network;
 *      the node learns its address from that. If nothing is heard within
 *      GV_FALLBACK_AFTER_MS, the node uses GV_FALLBACK_CONSOLE_IP instead.
 *
 * Printing every measurement to the serial port is OFF by default
 * (GV_SERIAL_ECHO 0). Set it to 1 if you want to compare the wired and
 * wireless data while setting up.
 * ========================================================================== */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/event_groups.h"

#include "nvs_flash.h"

#include "esp_mac.h"
#include "esp_timer.h"
#include "rom/ets_sys.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_now.h"

#include "lwip/inet.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"
#include "ping/ping_sock.h"

#include "protocol_examples_common.h"
#include "esp_csi_gain_ctrl.h"

/* Ping interval = 1000 / this, in ms.
 *
 * Measured on these boards: asking for 100 pings per second gave a rate
 * that wandered between 46 and 86 packets/s, with gaps clustered at both
 * 10 ms and 20 ms. Asking for 50 per second gave a steady rate.
 *
 * A wandering rate is worse than a slower one: the console's window is a
 * fixed NUMBER of packets, so its real duration in seconds -- and therefore
 * every calibrated threshold -- moves with the rate.
 *
 * The exact cause was not pinned down. An early explanation blamed a 10 ms
 * system tick, but this project builds with a 1 ms tick (CONFIG_FREERTOS_HZ
 * = 1000), so treat 50 as a measured, working value rather than a derived
 * one. 50 packets/s is far more than a human body needs.
 */
#define CONFIG_SEND_FREQUENCY      50
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
#define CSI_FORCE_LLTF                      0
#endif
#define CONFIG_FORCE_GAIN                   0

#if CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
#define CONFIG_GAIN_CONTROL                 1
#endif

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(6, 0, 0)
#define ESP_IF_WIFI_STA ESP_MAC_WIFI_STA
#endif

/* ==========================================================================
 * GHOST-VISION CONFIGURATION
 * ==========================================================================
 *  >>> GV_NODE_ID is THE ONE LINE YOU CHANGE per board. 1, 2, 3 or 4. <<<
 *      Flash N1 with 1, N2 with 2, and so on.  The boot log prints it in a
 *      banner so you can always confirm what a board thinks it is.
 * ========================================================================== */
#define GV_NODE_ID                 4

/* Protocol 2 adds the link id. Format 1 had a pad byte in every record that
 * was always zero; that byte now carries WHICH transmitter the CSI came from,
 * so the packet does not grow by a single byte. */
#define GV_PROTO_VERSION           2

/* ==========================================================================
 * MULTI-LINK SENSING
 *
 * Every node already pings the router 50x/s and uploads CSI ~13x/s. Those
 * transmissions are already in the air. Until now each node ignored all of
 * them and listened only to the router, giving 4 sensing paths:
 *
 *      router->N1   router->N2   router->N3   router->N4
 *
 * With promiscuous mode a node also hears its three siblings, adding six
 * more physical paths (each measured from both ends, so twelve directed
 * measurements):
 *
 *      N1<->N2  N1<->N3  N1<->N4  N2<->N3  N2<->N4  N3<->N4
 *
 * Ten physical paths instead of four, for zero extra transmissions, zero
 * extra airtime and zero extra battery. That is 2.5x the geometric
 * information the position solver has to work with.
 *
 * Link ids on the wire:  0 = router,  1..4 = node N1..N4
 * ========================================================================== */
#define GV_MULTILINK               1
#define GV_LINKS                   5     /* router + 4 nodes */
#define GV_DATA_PORT               8266     /* node  -> console (CSI)        */
#define GV_DISCOVERY_PORT          8267     /* console -> node ("I am here") */
#define GV_FALLBACK_CONSOLE_IP     "10.8.0.50"
#define GV_FALLBACK_AFTER_MS       10000    /* wait this long for a beacon   */
#define GV_HELLO_MAGIC             "GHOSTVISION"

#define GV_MAX_CSI                 128      /* measured: len is always 128   */
#define GV_RECS_PER_DGRAM          8        /* multi-link makes ~4x the records */
/* Queue was sized for one link at 50/s. Four links is ~240 records/s, and the
 * sender only guarantees a flush every GV_FLUSH_MS, so 48 slots would overflow
 * on any hiccup. 192 is ~0.8 s of headroom and costs 26 KB of the 286 KB free. */
#define GV_QUEUE_LEN               192
#define GV_FLUSH_MS                150      /* send a part-full datagram     */
#define GV_SIGLEN_LEARN            60       /* records used to learn the fingerprint */

/* ==========================================================================
 * DECIMATION -- deliberately throw data away.
 *
 * Multi-link capture produced ~1500 records/s per node, about 6 Mbps of
 * uploads across four nodes on one 20 MHz channel. Packet loss appeared for
 * the first time in this project (N1 at 5.5%).
 *
 * A human body does not move fast enough to need 500 samples/s on a path.
 * 50/s was validated end to end at 0.00% loss. Keeping one record in N gets
 * the rate back into that proven range and hands the airtime back.
 *
 * Sibling links are the loud ones (~500/s each) so they are cut hard.
 * The router link is already modest (~100-190/s) so it is barely cut.
 * ========================================================================== */
/* RATE CAP, not a fixed divider.
 *
 * A fixed "keep 1 in N" needs to know the incoming rate in advance, and the
 * incoming rate turned out to be wildly unstable: the same transmitter was
 * heard at 49/s by one node and 0/s by another, and swung 36/s -> 5/s within
 * seconds as WiFi rate adaptation changed its transmission speed.
 *
 * A per-link cap is self-regulating. A loud link is trimmed to the cap; a
 * quiet link is passed through untouched. No prediction required, and a hard
 * upper bound on airtime whatever the radio decides to do. */
#define GV_MAX_PER_SEC             80       /* records per second, per link */
#define GV_STATS_EVERY_MS          5000

#define GV_SERIAL_ECHO             0        /* 1 = also print CSI to serial  */

/* ==========================================================================
 * GV_PROBE  --  TEMPORARY EXPERIMENT, set back to 0 when finished.
 *
 * Question it answers: can one node overhear the OTHER nodes' packets and
 * get CSI from them?
 *
 * Every node already pings the router 50x/second. Those packets are already
 * in the air. Right now each node ignores everything except the router, so
 * we have 4 sensing paths (each node <-> router). If a node can also hear
 * its three siblings, we get 6 more paths for free -- no extra transmissions,
 * no extra airtime, no extra battery.
 *
 * In probe mode the node does NOT stream CSI. It listens to everything on
 * the channel and prints a table of who it heard. That is all.
 *
 * It also disables WiFi power save. The radio currently naps between
 * packets ("wifi:pm start" in the boot log), which is a prime suspect for
 * the packet-rate dips that cause phantom detections.
 * ========================================================================== */
#define GV_PROBE                   0

static const char *TAG = "csi_recv_router";

#if GV_PROBE
/* ---- probe bookkeeping: who did we hear, and how often ---- */
#define GV_PROBE_SLOTS 14
typedef struct {
    uint8_t  mac[6];
    uint32_t n;
    uint16_t sig_len;
    int8_t   rssi;
    uint16_t len;
} gv_probe_t;
static gv_probe_t s_probe[GV_PROBE_SLOTS];
static int        s_probe_n = 0;
static uint8_t    GV_PROBE_BSSID[6] = {0};

/* Fill these in with YOUR four board MACs so the table can label them. */
static const uint8_t GV_KNOWN[4][6] = {
    {0x02, 0x00, 0x00, 0x00, 0x00, 0x01},   /* N1  <- REPLACE with this board's MAC */
    {0x02, 0x00, 0x00, 0x00, 0x00, 0x02},   /* N2  <- REPLACE with this board's MAC */
    {0x02, 0x00, 0x00, 0x00, 0x00, 0x03},   /* N3  <- REPLACE with this board's MAC */
    {0x02, 0x00, 0x00, 0x00, 0x00, 0x04},   /* N4  <- REPLACE with this board's MAC */
};

static int gv_which_node(const uint8_t *mac)
{
    for (int i = 0; i < 4; i++) {
        if (!memcmp(mac, GV_KNOWN[i], 6)) return i + 1;
    }
    return 0;
}

static void gv_probe_note(const uint8_t *mac, uint16_t sig_len, int8_t rssi, uint16_t len)
{
    for (int i = 0; i < s_probe_n; i++) {
        if (!memcmp(s_probe[i].mac, mac, 6)) {
            s_probe[i].n++;
            s_probe[i].sig_len = sig_len;
            s_probe[i].rssi = rssi;
            s_probe[i].len = len;
            return;
        }
    }
    if (s_probe_n < GV_PROBE_SLOTS) {
        memcpy(s_probe[s_probe_n].mac, mac, 6);
        s_probe[s_probe_n].n = 1;
        s_probe[s_probe_n].sig_len = sig_len;
        s_probe[s_probe_n].rssi = rssi;
        s_probe[s_probe_n].len = len;
        s_probe_n++;
    }
}

static void gv_probe_report_task(void *arg)
{
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(5000));
        ESP_LOGW(TAG, "---- PROBE: transmitters heard in the last 5 s ----");
        ESP_LOGW(TAG, "     %-18s %8s %7s %6s %5s  %s",
                 "MAC", "packets", "sig_len", "len", "rssi", "who");
        for (int i = 0; i < s_probe_n; i++) {
            int node = gv_which_node(s_probe[i].mac);
            char who[16];
            if (node) {
                snprintf(who, sizeof(who), "NODE N%d  <<<<", node);
            } else if (!memcmp(s_probe[i].mac, GV_PROBE_BSSID, 6)) {
                snprintf(who, sizeof(who), "our router");
            } else {
                snprintf(who, sizeof(who), "stranger");
            }
            ESP_LOGW(TAG, "     " MACSTR " %8lu %7u %6u %5d  %s",
                     MAC2STR(s_probe[i].mac), (unsigned long)s_probe[i].n,
                     s_probe[i].sig_len, s_probe[i].len, s_probe[i].rssi, who);
            s_probe[i].n = 0;
        }
        if (!s_probe_n) {
            ESP_LOGE(TAG, "     nothing heard at all -- promiscuous CSI is not working");
        }
    }
}

#endif /* GV_PROBE */

/* Required by the promiscuous API, but CSI arrives through its own callback,
   so there is deliberately nothing to do here. Keep it empty and fast. */
static void gv_promisc_cb(void *buf, wifi_promiscuous_pkt_type_t type)
{
    (void)buf; (void)type;
}

/* ---- Ghost-Vision state -------------------------------------------------- */
typedef struct {
    uint32_t seq;
    int8_t   rssi;
    uint8_t  len;
    uint8_t  link;              /* 0 = router, 1..4 = which node transmitted */
    int8_t   csi[GV_MAX_CSI];
} gv_rec_t;

/* The four boards, so a node can recognise its siblings on air.
 * REPLACE these placeholders with your own boards' MACs: flash each board,
 * press RESET, and copy the 'sta' MAC from its boot banner.
 * If a board is swapped, update this table or it becomes invisible to the mesh. */
static const uint8_t GV_NODE_MAC[4][6] = {
    {0x02, 0x00, 0x00, 0x00, 0x00, 0x01},   /* N1  <- REPLACE with this board's MAC */
    {0x02, 0x00, 0x00, 0x00, 0x00, 0x02},   /* N2  <- REPLACE with this board's MAC */
    {0x02, 0x00, 0x00, 0x00, 0x00, 0x03},   /* N3  <- REPLACE with this board's MAC */
    {0x02, 0x00, 0x00, 0x00, 0x00, 0x04},   /* N4  <- REPLACE with this board's MAC */
};
static uint8_t s_router_mac[6] = {0};
static volatile uint32_t s_link_count[GV_LINKS] = {0};
static uint32_t s_link_win_n[GV_LINKS] = {0};      /* records this second */
static int64_t  s_link_win_t[GV_LINKS] = {0};      /* when this second began */
static volatile uint32_t s_link_kept[GV_LINKS] = {0};
static volatile uint32_t s_ping_ok = 0;
static volatile uint32_t s_ping_lost = 0;

static QueueHandle_t   s_gv_queue      = NULL;
static volatile uint32_t s_gv_console_ip = 0;      /* network byte order      */
static volatile uint32_t s_gv_seq       = 0;
static volatile uint32_t s_gv_drop_queue = 0;      /* queue was full          */
static volatile uint32_t s_gv_drop_frame = 0;      /* wrong sig_len (beacons) */
static volatile uint32_t s_gv_sent       = 0;      /* datagrams sent          */
static volatile uint32_t s_gv_clipped    = 0;      /* CSI values that saturated */

/* sig_len must be learned SEPARATELY PER LINK. The router's ping replies and
 * a sibling's uploads are different frame types with different lengths, so one
 * global value would throw away every link but one. */
static uint16_t s_gv_siglen_samples[GV_LINKS][GV_SIGLEN_LEARN];
static int      s_gv_siglen_n[GV_LINKS];
static int      s_gv_siglen_locked[GV_LINKS];      /* -1 = still learning     */
static int      s_gv_lock_tries[GV_LINKS];

static int gv_link_of(const uint8_t *mac)
{
    if (s_router_mac[0] && !memcmp(mac, s_router_mac, 6)) return 0;
#if GV_MULTILINK
    for (int i = 0; i < 4; i++) {
        if (!memcmp(mac, GV_NODE_MAC[i], 6)) {
            int link = i + 1;
            if (link == GV_NODE_ID) return -1;      /* never hear yourself */
            return link;
        }
    }
#endif
    return -1;
}

/* --------------------------------------------------------------------------
 * Learn which sig_len the ping replies use, then reject everything else.
 * Called from the CSI callback.  Returns true if this frame should be kept.
 * -------------------------------------------------------------------------- */
/* How dominant the winning value must be before we trust it. A lock at 12%
 * is not a fingerprint, it is a guess -- and a wrong guess silently bins the
 * whole link, which is exactly what happened to the router link. */
#define GV_LOCK_MIN_PCT   40
#define GV_LOCK_TRIES     3

static bool gv_frame_is_wanted(int link, uint16_t key)
{
    if (link < 0 || link >= GV_LINKS) return false;
    if (s_gv_siglen_locked[link] < 0) {
        if (s_gv_siglen_n[link] < GV_SIGLEN_LEARN) {
            s_gv_siglen_samples[link][s_gv_siglen_n[link]++] = key;
            return true;                    /* pass everything while learning */
        }
        /* Simple mode (most common value).  Ping replies outnumber beacons
           roughly 50:1, so this cannot pick the wrong one.                   */
        int best = s_gv_siglen_samples[link][0], best_count = 0;
        int second = -1, second_count = 0;
        for (int i = 0; i < GV_SIGLEN_LEARN; i++) {
            int c = 0;
            for (int j = 0; j < GV_SIGLEN_LEARN; j++) {
                if (s_gv_siglen_samples[link][j] == s_gv_siglen_samples[link][i]) c++;
            }
            if (c > best_count) {
                second = best; second_count = best_count;
                best_count = c; best = s_gv_siglen_samples[link][i];
            } else if (c > second_count && s_gv_siglen_samples[link][i] != best) {
                second_count = c; second = s_gv_siglen_samples[link][i];
            }
        }

        /* A weak winner means the link is carrying mixed frame formats and we
           have identified nothing. Throw the window away and listen again
           rather than locking onto noise. */
        if (best_count * 100 < GV_LOCK_MIN_PCT * GV_SIGLEN_LEARN
            && s_gv_lock_tries[link] < GV_LOCK_TRIES) {
            s_gv_lock_tries[link]++;
            s_gv_siglen_n[link] = 0;
            ESP_LOGW(TAG, "GV: link %d weak fingerprint (best %d = %d%%, next "
                          "%d = %d%%). Re-learning, attempt %d of %d.",
                     link, best, best_count, second, second_count,
                     s_gv_lock_tries[link], GV_LOCK_TRIES);
            return true;
        }

        s_gv_siglen_locked[link] = best;
        ESP_LOGW(TAG, "GV: link %d locked to rate %d (%d%% of frames; "
                      "runner-up rate %d at %d%%).",
                 link, best, best_count, second, second_count);
        if (best_count * 100 < GV_LOCK_MIN_PCT * GV_SIGLEN_LEARN) {
            ESP_LOGE(TAG, "GV: link %d fingerprint still weak after %d tries -- "
                          "this link may be unreliable.", link, GV_LOCK_TRIES);
        }
    }
    if (key != (uint16_t)s_gv_siglen_locked[link]) {
        s_gv_drop_frame++;
        return false;
    }
    return true;
}

/* --------------------------------------------------------------------------
 * Discovery task: listen for the console announcing itself, learn its address.
 * -------------------------------------------------------------------------- */
static void gv_discovery_task(void *arg)
{
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sock < 0) {
        ESP_LOGE(TAG, "GV: discovery socket failed (errno %d)", errno);
        vTaskDelete(NULL);
        return;
    }

    struct sockaddr_in bind_addr = {
        .sin_family      = AF_INET,
        .sin_port        = htons(GV_DISCOVERY_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (bind(sock, (struct sockaddr *)&bind_addr, sizeof(bind_addr)) < 0) {
        ESP_LOGE(TAG, "GV: discovery bind failed (errno %d)", errno);
        close(sock);
        vTaskDelete(NULL);
        return;
    }
    ESP_LOGI(TAG, "GV: listening for console announcements on UDP %d",
             GV_DISCOVERY_PORT);

    char buf[64];
    while (1) {
        struct sockaddr_in src;
        socklen_t slen = sizeof(src);
        int r = recvfrom(sock, buf, sizeof(buf) - 1, 0,
                         (struct sockaddr *)&src, &slen);
        if (r <= 0) {
            vTaskDelay(pdMS_TO_TICKS(200));
            continue;
        }
        buf[r] = '\0';
        if (strncmp(buf, GV_HELLO_MAGIC, strlen(GV_HELLO_MAGIC)) != 0) {
            continue;
        }
        if (s_gv_console_ip != src.sin_addr.s_addr) {
            s_gv_console_ip = src.sin_addr.s_addr;
            char ipstr[16];
            inet_ntoa_r(src.sin_addr, ipstr, sizeof(ipstr));
            ESP_LOGW(TAG, "GV: console found at %s -- streaming there now", ipstr);
        }
    }
}

/* --------------------------------------------------------------------------
 * Sender task: drain the queue, pack records, send them.
 *
 * Wire format (little-endian):
 *   offset 0   'G'
 *   offset 1   'V'
 *   offset 2   protocol version
 *   offset 3   node id
 *   offset 4   number of records in this datagram
 *   offset 5   bytes of CSI per record (128)
 *   offset 6-7 reserved
 *   offset 8   uint32 sequence number of the FIRST record
 *   then, per record:  int8 rssi, 1 pad byte, then the CSI bytes
 * -------------------------------------------------------------------------- */
#define GV_HDR_BYTES  12
#define GV_REC_BYTES  (2 + GV_MAX_CSI)

static void gv_sender_task(void *arg)
{
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sock < 0) {
        ESP_LOGE(TAG, "GV: send socket failed (errno %d)", errno);
        vTaskDelete(NULL);
        return;
    }

    static uint8_t dgram[GV_HDR_BYTES + GV_RECS_PER_DGRAM * GV_REC_BYTES];
    dgram[0] = 'G';
    dgram[1] = 'V';
    dgram[2] = GV_PROTO_VERSION;
    dgram[3] = GV_NODE_ID;
    dgram[5] = GV_MAX_CSI;
    dgram[6] = 0;
    dgram[7] = 0;

    int      n_recs    = 0;
    uint32_t first_seq = 0;
    int64_t  last_flush = esp_timer_get_time();
    int64_t  last_stats = esp_timer_get_time();
    bool     announced_fallback = false;

    gv_rec_t rec;

    while (1) {
        bool have = (xQueueReceive(s_gv_queue, &rec,
                                   pdMS_TO_TICKS(GV_FLUSH_MS)) == pdTRUE);
        int64_t now_us = esp_timer_get_time();

        if (have) {
            if (n_recs == 0) first_seq = rec.seq;
            uint8_t *p = &dgram[GV_HDR_BYTES + n_recs * GV_REC_BYTES];
            p[0] = (uint8_t)rec.rssi;
            p[1] = rec.link;        /* was a pad byte in protocol 1 */
            memcpy(&p[2], rec.csi, GV_MAX_CSI);
            n_recs++;
        }

        bool timed_out = (now_us - last_flush) > (GV_FLUSH_MS * 1000);
        if (n_recs > 0 && (n_recs >= GV_RECS_PER_DGRAM || timed_out)) {

            uint32_t ip = s_gv_console_ip;
            if (ip == 0) {
                if ((now_us / 1000) > GV_FALLBACK_AFTER_MS) {
                    ip = inet_addr(GV_FALLBACK_CONSOLE_IP);
                    s_gv_console_ip = ip;
                    if (!announced_fallback) {
                        announced_fallback = true;
                        ESP_LOGW(TAG, "GV: no console announcement heard -- "
                                      "falling back to %s", GV_FALLBACK_CONSOLE_IP);
                    }
                } else {
                    /* Still early in boot. Discard rather than pile up. */
                    n_recs = 0;
                    last_flush = now_us;
                    continue;
                }
            }

            dgram[4] = (uint8_t)n_recs;
            dgram[8]  = (uint8_t)(first_seq       & 0xFF);
            dgram[9]  = (uint8_t)((first_seq >> 8)  & 0xFF);
            dgram[10] = (uint8_t)((first_seq >> 16) & 0xFF);
            dgram[11] = (uint8_t)((first_seq >> 24) & 0xFF);

            struct sockaddr_in dst = {
                .sin_family      = AF_INET,
                .sin_port        = htons(GV_DATA_PORT),
                .sin_addr.s_addr = ip,
            };
            int len = GV_HDR_BYTES + n_recs * GV_REC_BYTES;
            int w = sendto(sock, dgram, len, 0,
                           (struct sockaddr *)&dst, sizeof(dst));
            if (w < 0) {
                ESP_LOGE(TAG, "GV: sendto failed (errno %d)", errno);
                vTaskDelay(pdMS_TO_TICKS(200));
            } else {
                s_gv_sent++;
            }
            n_recs = 0;
            last_flush = now_us;
        }

        if ((now_us - last_stats) > (int64_t)GV_STATS_EVERY_MS * 1000) {
            last_stats = now_us;
            char ipstr[16] = "none";
            if (s_gv_console_ip) {
                struct in_addr a = { .s_addr = s_gv_console_ip };
                inet_ntoa_r(a, ipstr, sizeof(ipstr));
            }
            ESP_LOGI(TAG, "GV N%d -> %s | dgrams %lu | records %lu | "
                          "queue-drop %lu | clipped %lu",
                     GV_NODE_ID, ipstr,
                     (unsigned long)s_gv_sent,
                     (unsigned long)s_gv_seq,
                     (unsigned long)s_gv_drop_queue,
                     (unsigned long)s_gv_clipped);
            /* Per-link rates. This is the whole point of the build: if the
               sibling links are alive these numbers are all non-zero. */
            ESP_LOGI(TAG, "   heard/5s  router:%lu  N1:%lu  N2:%lu  N3:%lu  N4:%lu"
                          "   fp[%d,%d,%d,%d,%d]",
                     (unsigned long)s_link_count[0], (unsigned long)s_link_count[1],
                     (unsigned long)s_link_count[2], (unsigned long)s_link_count[3],
                     (unsigned long)s_link_count[4],
                     s_gv_siglen_locked[0], s_gv_siglen_locked[1],
                     s_gv_siglen_locked[2], s_gv_siglen_locked[3],
                     s_gv_siglen_locked[4]);
            /* Ping health answers the router mystery directly instead of by
               inference: are replies coming back, or timing out? */
            ESP_LOGI(TAG, "   ping replies %lu, timeouts %lu, wrong-frame drops %lu",
                     (unsigned long)s_ping_ok, (unsigned long)s_ping_lost,
                     (unsigned long)s_gv_drop_frame);
            ESP_LOGI(TAG, "   kept/5s   router:%lu  N1:%lu  N2:%lu  N3:%lu  N4:%lu",
                     (unsigned long)s_link_kept[0], (unsigned long)s_link_kept[1],
                     (unsigned long)s_link_kept[2], (unsigned long)s_link_kept[3],
                     (unsigned long)s_link_kept[4]);
            for (int i = 0; i < GV_LINKS; i++) { s_link_count[i] = 0; s_link_kept[i] = 0; }
        }
    }
}

static void gv_init(void)
{
    for (int i = 0; i < GV_LINKS; i++) {
        s_gv_siglen_locked[i] = -1;
        s_gv_siglen_n[i] = 0;
        s_gv_lock_tries[i] = 0;
        s_link_win_n[i] = 0;
        s_link_win_t[i] = 0;
        s_link_kept[i] = 0;
        s_link_count[i] = 0;
    }
    s_gv_queue = xQueueCreate(GV_QUEUE_LEN, sizeof(gv_rec_t));
    if (!s_gv_queue) {
        ESP_LOGE(TAG, "GV: could not create queue -- out of memory");
        return;
    }
    xTaskCreate(gv_discovery_task, "gv_disc", 3584, NULL, 4, NULL);
    xTaskCreate(gv_sender_task,    "gv_send", 4096, NULL, 5, NULL);

    uint8_t mac[6] = {0};
    esp_wifi_get_mac(WIFI_IF_STA, mac);
    ESP_LOGW(TAG, "==========================================");
    ESP_LOGW(TAG, "  GHOST-VISION");
    ESP_LOGW(TAG, "  THIS BOARD IS NODE N%d", GV_NODE_ID);
    ESP_LOGW(TAG, "  MAC " MACSTR, MAC2STR(mac));
    ESP_LOGW(TAG, "  CSI -> UDP %d   discovery <- UDP %d",
             GV_DATA_PORT, GV_DISCOVERY_PORT);
    ESP_LOGW(TAG, "==========================================");
}

/* ========================================================================== */

static void wifi_csi_rx_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf) {
        ESP_LOGW(TAG, "<%s> wifi_csi_cb", esp_err_to_name(ESP_ERR_INVALID_ARG));
        return;
    }

#if GV_PROBE
    /* Probe mode: accept EVERY transmitter, tally, and do nothing else. */
    gv_probe_note(info->mac, info->rx_ctrl.sig_len,
                  (int8_t)info->rx_ctrl.rssi, (uint16_t)info->len);
    return;
#endif

    /* Which of our known transmitters is this? Anything else -- neighbours,
       phones, other networks on channel 6 -- is discarded here, cheaply. */
    int gv_link = gv_link_of(info->mac);
    if (gv_link < 0) {
        return;
    }

    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;

    /* ---- GV: reject beacons and other non-data frames, per link ---------- */
    /* Fingerprint on the PHY RATE, not the frame length.
     *
     * CSI is measured from the packet's preamble, so two frames sent at the
     * same rate give structurally comparable CSI however long they are. Length
     * was only ever a proxy for "same frame format", and it broke the moment
     * this node began overhearing the router talking to OTHER nodes: all
     * different lengths, but every one of them travelled the same router->here
     * path through the room and is a perfectly good measurement of it.
     *
     * Beacons -- the original contamination -- are sent at a different rate,
     * so they are still excluded. */
    /* Composite fingerprint: PHY MODE + BANDWIDTH + RATE, not rate alone.
     *
     * The rate field is reused: in legacy mode it is a legacy rate index, in
     * HT mode it carries an MCS index. So two structurally different frame
     * formats can both report "11" and get averaged into one measurement.
     * Sibling links never showed this because a node always transmits the
     * same way -- but the router talks to four different clients and can
     * switch format per client. That is the most likely cause of router>N1
     * reading a wobble of ~500 while every sibling path read 5-10.
     *
     * Packing mode and bandwidth into the key makes those separate formats
     * separate fingerprints, so only one of them survives the filter. */
    /* In 802.11n (sig_mode 1) the CSI comes from the HT-LTF preamble, which is
     * identical whatever MCS the payload uses. So for HT frames the rate/MCS
     * is deliberately EXCLUDED from the fingerprint -- otherwise ordinary rate
     * adaptation looks like a different frame type and the link starves. That
     * is what dropped N2>N1 to 0/s while N2>N3 ran at 49/s.
     *
     * Legacy frames (sig_mode 0) keep the rate in the key, because that is
     * where the router link lives and where beacons -- sent at a different
     * legacy rate -- must stay excluded. That link is already stable. */
    uint16_t gv_key = (uint16_t)(((rx_ctrl->sig_mode & 0x3) << 12)
                               | ((rx_ctrl->cwb      & 0x3) << 10)
                               | ((rx_ctrl->sig_mode & 0x3) ? 0u
                                                            : (rx_ctrl->rate & 0x3FFu)));
    if (!gv_frame_is_wanted(gv_link, gv_key)) {
        return;
    }
    s_link_count[gv_link]++;

    /* Per-link rate cap. Applied AFTER filtering so the "heard" counters still
       reflect real link health. A quiet link passes through untouched. */
    {
        int64_t now_us = esp_timer_get_time();
        if (now_us - s_link_win_t[gv_link] >= 1000000) {
            s_link_win_t[gv_link] = now_us;
            s_link_win_n[gv_link] = 0;
        }
        if (s_link_win_n[gv_link] >= GV_MAX_PER_SEC) {
            return;
        }
        s_link_win_n[gv_link]++;
        s_link_kept[gv_link]++;
    }

    static int s_count = 0;
    float compensate_gain = 1.0f;
    static uint8_t agc_gain = 0;
    static int8_t fft_gain = 0;
#if CONFIG_GAIN_CONTROL
    static uint8_t agc_gain_baseline = 0;
    static int8_t fft_gain_baseline = 0;
    esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);
    if (s_count < 100) {
        esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
    } else if (s_count == 100) {
        esp_csi_gain_ctrl_get_rx_gain_baseline(&agc_gain_baseline, &fft_gain_baseline);
#if CONFIG_FORCE_GAIN
        esp_csi_gain_ctrl_set_rx_force_gain(agc_gain_baseline, fft_gain_baseline);
        ESP_LOGI(TAG, "fft_force %d, agc_force %d", fft_gain_baseline, agc_gain_baseline);
#endif
    }
    esp_csi_gain_ctrl_get_gain_compensation(&compensate_gain, agc_gain, fft_gain);
    ESP_LOGD(TAG, "compensate_gain %f, agc_gain %d, fft_gain %d", compensate_gain, agc_gain, fft_gain);
#endif

    /* ---- GV: hand a copy to the sender task ------------------------------
       This must not block.  If the queue is full we drop and count it.
       (Assumes the standard S3 layout: info->buf is int8 I/Q pairs.)        */
    if (s_gv_queue) {
        gv_rec_t r;
        int L = info->len;
        if (L > GV_MAX_CSI) L = GV_MAX_CSI;
        r.seq  = s_gv_seq++;
        r.rssi = (int8_t)rx_ctrl->rssi;
        r.len  = (uint8_t)L;
        r.link = (uint8_t)gv_link;
        for (int i = 0; i < L; i++) {
            int v = (int)(compensate_gain * info->buf[i]);
            if (v >  127) { v =  127; s_gv_clipped++; }
            if (v < -128) { v = -128; s_gv_clipped++; }
            r.csi[i] = (int8_t)v;
        }
        for (int i = L; i < GV_MAX_CSI; i++) {
            r.csi[i] = 0;
        }
        if (xQueueSend(s_gv_queue, &r, 0) != pdTRUE) {
            s_gv_drop_queue++;
        }
    }

#if GV_SERIAL_ECHO
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
    if (!s_count) {
        ESP_LOGI(TAG, "================ CSI RECV ================");
        ets_printf("type,seq,mac,rssi,rate,noise_floor,fft_gain,agc_gain,channel,local_timestamp,sig_len,rx_format,len,first_word,data\n");
    }
    ets_printf("CSI_DATA,%d," MACSTR ",%d,%d,%d,%d,%d,%d,%d,%d,%d",
               s_count, MAC2STR(info->mac), rx_ctrl->rssi, rx_ctrl->rate,
               rx_ctrl->noise_floor, fft_gain, agc_gain, rx_ctrl->channel,
               rx_ctrl->timestamp, rx_ctrl->sig_len, rx_ctrl->cur_bb_format);
#else
    if (!s_count) {
        ESP_LOGI(TAG, "================ CSI RECV ================");
        ets_printf("type,id,mac,rssi,rate,sig_mode,mcs,bandwidth,smoothing,not_sounding,aggregation,stbc,fec_coding,sgi,noise_floor,ampdu_cnt,channel,secondary_channel,local_timestamp,ant,sig_len,rx_format,len,first_word,data\n");
    }
    ets_printf("CSI_DATA,%d," MACSTR ",%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d",
               s_count, MAC2STR(info->mac), rx_ctrl->rssi, rx_ctrl->rate, rx_ctrl->sig_mode,
               rx_ctrl->mcs, rx_ctrl->cwb, rx_ctrl->smoothing, rx_ctrl->not_sounding,
               rx_ctrl->aggregation, rx_ctrl->stbc, rx_ctrl->fec_coding, rx_ctrl->sgi,
               rx_ctrl->noise_floor, rx_ctrl->ampdu_cnt, rx_ctrl->channel, rx_ctrl->secondary_channel,
               rx_ctrl->timestamp, rx_ctrl->ant, rx_ctrl->sig_len, rx_ctrl->sig_mode);
#endif

#if (CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61) && CSI_FORCE_LLTF

    int16_t csi = ((int16_t)(((((uint16_t)info->buf[1]) << 8) | info->buf[0]) << 4) >> 4);
    ets_printf(",%d,%d,\"[%d", (info->len - 2) / 2, info->first_word_invalid, (int16_t)(compensate_gain * csi));
    for (int i = 2; i < (info->len - 2); i += 2) {
        csi = ((int16_t)(((((uint16_t)info->buf[i + 1]) << 8) | info->buf[i]) << 4) >> 4);
        ets_printf(",%d", (int16_t)(compensate_gain * csi));
    }

#else
    ets_printf(",%d,%d,\"[%d", info->len, info->first_word_invalid, (int16_t)(compensate_gain * info->buf[0]));
    for (int i = 1; i < info->len; i++) {
        ets_printf(",%d", (int16_t)(compensate_gain * info->buf[i]));
    }
#endif
    ets_printf("]\"\n");
#endif /* GV_SERIAL_ECHO */

    s_count++;
}

static void wifi_csi_init()
{
    /**
     * @brief In order to ensure the compatibility of routers, only LLTF sub-carriers are selected.
     */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
    wifi_csi_config_t csi_config = {
        .enable                   = true,
        .acquire_csi_legacy       = true,
        .acquire_csi_force_lltf   = CSI_FORCE_LLTF,
        .acquire_csi_ht20         = true,
        .acquire_csi_ht40         = true,
        .acquire_csi_vht          = false,
        .acquire_csi_su           = false,
        .acquire_csi_mu           = false,
        .acquire_csi_dcm          = false,
        .acquire_csi_beamformed   = false,
        .acquire_csi_he_stbc_mode = 2,
        .val_scale_cfg            = 0,
        .dump_ack_en              = false,
        .reserved                 = false
    };
#elif CONFIG_IDF_TARGET_ESP32C6
    wifi_csi_config_t csi_config = {
        .enable                 = true,
        .acquire_csi_legacy     = true,
        .acquire_csi_ht20       = true,
        .acquire_csi_ht40       = true,
        .acquire_csi_su         = false,
        .acquire_csi_mu         = false,
        .acquire_csi_dcm        = false,
        .acquire_csi_beamformed = false,
        .acquire_csi_he_stbc    = 2,
        .val_scale_cfg          = false,
        .dump_ack_en            = false,
        .reserved               = false
    };
#else
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,
        .htltf_en          = false,
        .stbc_htltf2_en    = false,
        .ltf_merge_en      = true,
        .channel_filter_en = true,
        .manu_scale        = true,
        .shift             = true,
    };
#endif
    static wifi_ap_record_t s_ap_info = {0};
    ESP_ERROR_CHECK(esp_wifi_sta_get_ap_info(&s_ap_info));
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    memcpy(s_router_mac, s_ap_info.bssid, 6);
#if GV_PROBE
    memcpy(GV_PROBE_BSSID, s_ap_info.bssid, 6);
#endif
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(wifi_csi_rx_cb, s_ap_info.bssid));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

static void gv_ping_success(esp_ping_handle_t hdl, void *args) { (void)hdl; (void)args; s_ping_ok++; }
static void gv_ping_timeout(esp_ping_handle_t hdl, void *args) { (void)hdl; (void)args; s_ping_lost++; }

static esp_err_t wifi_ping_router_start()
{
    static esp_ping_handle_t ping_handle = NULL;

    esp_ping_config_t ping_config = ESP_PING_DEFAULT_CONFIG();
    ping_config.count             = 0;
    ping_config.interval_ms       = 1000 / CONFIG_SEND_FREQUENCY;
    ping_config.task_stack_size   = 3072;
    ping_config.data_size         = 1;

    esp_netif_ip_info_t local_ip;
    esp_netif_get_ip_info(esp_netif_get_handle_from_ifkey("WIFI_STA_DEF"), &local_ip);
    ESP_LOGI(TAG, "got ip:" IPSTR ", gw: " IPSTR, IP2STR(&local_ip.ip), IP2STR(&local_ip.gw));
    ping_config.target_addr.u_addr.ip4.addr = ip4_addr_get_u32(&local_ip.gw);
    ping_config.target_addr.type = ESP_IPADDR_TYPE_V4;

    esp_ping_callbacks_t cbs = { 0 };
    cbs.on_ping_success = gv_ping_success;
    cbs.on_ping_timeout = gv_ping_timeout;
    esp_ping_new_session(&ping_config, &cbs, &ping_handle);
    esp_ping_start(ping_handle);

    return ESP_OK;
}

void app_main()
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    /**
     * @brief This helper function configures Wi-Fi, as selected in menuconfig.
     *        Read "Establishing Wi-Fi Connection" section in esp-idf/examples/protocols/README.md
     *        for more information about this function.
     */
    ESP_ERROR_CHECK(example_connect());

#if GV_PROBE
    /* Stop the radio napping. Power save makes the chip miss packets that
       arrive while it is asleep -- prime suspect for the rate dips. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    /* Listen to everything on the channel, not just packets addressed to us. */
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_rx_cb(gv_promisc_cb));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
    wifi_promiscuous_filter_t pf = { .filter_mask = WIFI_PROMIS_FILTER_MASK_DATA };
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_filter(&pf));

    ESP_LOGW(TAG, "==========================================");
    ESP_LOGW(TAG, "  PROBE MODE -- not streaming CSI.");
    ESP_LOGW(TAG, "  Power save OFF, listening to all frames ON.");
    ESP_LOGW(TAG, "  Table every 5 s. Set GV_PROBE 0 to go back.");
    ESP_LOGW(TAG, "==========================================");
    xTaskCreate(gv_probe_report_task, "gv_probe", 3584, NULL, 3, NULL);
    wifi_csi_init();
    wifi_ping_router_start();
    return;
#endif

#if GV_MULTILINK
    /* Power save makes the radio nap between packets. A napping radio misses
       sibling transmissions, and the resulting rate dips are the prime suspect
       for phantom detections. Costs battery; buys steady timing. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));

    /* Hear packets that are not addressed to us -- this is what turns four
       sensing paths into ten. Filter to data frames so beacons and management
       chatter never reach the CSI callback. */
    /* Listen to EVERYTHING, not just frames we classify as data.
     *
     * MASK_DATA looked tidy, but with it the router link collapsed from 50
     * frames/s to about 1 while ping replies kept arriving at 50/s -- the
     * packets were reaching the chip but producing no CSI. An access point
     * labels its downlink frames differently from the way a station labels
     * its uplink, so a data-only mask can silently exclude exactly the
     * direction we care about.
     *
     * The rate fingerprint is now the thing that separates useful frames from
     * beacons, and it locks at 97-100% agreement, so it no longer needs help
     * from a hardware filter. Unknown transmitters are rejected in the first
     * two lines of the callback, so the extra load is negligible. */
    wifi_promiscuous_filter_t gv_pf = { .filter_mask = WIFI_PROMIS_FILTER_MASK_ALL };
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_filter(&gv_pf));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous_rx_cb(gv_promisc_cb));
    ESP_ERROR_CHECK(esp_wifi_set_promiscuous(true));
#endif

    gv_init();              /* GV: queue + sender + discovery, before CSI starts */
    wifi_csi_init();
    wifi_ping_router_start();
}
