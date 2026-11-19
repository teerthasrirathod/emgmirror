// emg_esp32_v2: EMG acquisition for a classic ESP32 (ESP32-D0WD-V3 DevKit), 1 to 6 ADC1 channels.
//
// Protocol: EMGM/2, a framed binary stream specified in docs/ACQUISITION_PROTOCOL.md.
// Every packet is COBS-encoded and ends with a 0x00 byte, carries a type byte and a CRC-16, so the
// host can resynchronise after any corruption and can tell data from diagnostics without guessing.
//
// Channel map: the GPIOs sampled, in column order, are set at run time with `PINS` and stored in
// NVS, so they survive a reboot. Every HELLO reports them. Only ADC1 pins are accepted
// (GPIO32, 33, 34, 35, 36/VP, 39/VN), so the radio could never block the ADC. The pins are only ever
// used as ADC inputs: nothing is driven.
// Default when nothing is stored: GPIO35, 32, 33. The host configuration (hardware.channels) is
// the source of truth and is applied with PINS when the HELLO differs.
//
// Sampling:
//  - Hardware-timed by the ESP-IDF continuous ADC (DMA) driver. Sample spacing comes from the ADC
//    clock, not from the main loop or the serial link.
//  - Oversampling: every output sample per channel is the SUM of OS consecutive conversions of that
//    channel (OS is reported). The host divides by OS. Raw codes are never calibrated or clipped on
//    the device, so the stream is the original ADC data.
//  - A DMA frame holds exactly ROWS_PER_FRAME output rows. The conv-done ISR timestamps each frame
//    with esp_timer (microseconds since boot) and the pool-overflow ISR marks frames the driver had
//    to discard. The sample index counts discarded rows too, so a device-side loss shows up on the
//    host as an exact jump in the index, and a link-side loss as a jump in frame_seq.
//
// Host commands (ASCII lines ending in '\n', case-insensitive). Each is answered with an ACK packet.
//   INFO              re-send HELLO
//   STOP              stop sampling (no DATA packets)
//   START             start sampling (sample index restarts at 0, run number increments)
//   RATE <hz>         output rate per channel, 500..4000, restarts sampling. Below ~417 Hz even 16x
//                     oversampling cannot reach the ADC's 20 kHz minimum conversion rate.
//   PINS <g1,g2,...>  1 to 6 distinct ADC1 GPIOs in column order; stored in NVS; restarts sampling
//
// Link budget at 500000 baud (~50 kB/s): 3 ch at 2 kHz ~18 kB/s; 6 ch at 2 kHz ~30 kB/s.
//
// SAFETY: this firmware does not make the arrangement safe for electrodes on a person. See
// docs/ELECTRICAL_SAFETY.md before connecting anything to the body.

#include <esp_adc/adc_continuous.h>
#include <esp_adc/adc_cali.h>
#include <esp_adc/adc_cali_scheme.h>
#include <esp_timer.h>
#include <esp_mac.h>
#include <Preferences.h>

#define FW_VERSION "2.1.0"
#define MAX_CHANNELS 6
#define DEFAULT_RATE_HZ 2000
#define ROWS_PER_FRAME 10
#define BAUD 500000
#define MIN_CONV_HZ 20000      // ESP32 continuous ADC lower limit (SOC_ADC_SAMPLE_FREQ_THRES_LOW)
#define MAX_CONV_HZ 200000     // conservative upper limit for 12-bit conversions
#define MAX_OS 16              // 16 * 4095 = 65520 still fits in uint16
#define INVALID_SAMPLE 0xFFFF
#define STATUS_PERIOD_MS 1000

enum PacketType : uint8_t { PKT_HELLO = 0x01, PKT_DATA = 0x02, PKT_STATUS = 0x03, PKT_LOG = 0x04, PKT_ACK = 0x05 };
enum DataFlags : uint8_t { FLAG_INCOMPLETE_ROW = 0x01, FLAG_RUN_START = 0x02, FLAG_AFTER_DROP = 0x04 };

const int ADC1_PINS[] = {32, 33, 34, 35, 36, 39};
int pins[MAX_CHANNELS] = {35, 32, 33};
int nch = 3;
Preferences prefs;

// ---------- acquisition state ----------
adc_continuous_handle_t adc = NULL;
adc_channel_t chan_of[MAX_CHANNELS];
uint32_t rate_hz = DEFAULT_RATE_HZ;
uint32_t os = 4;
uint32_t frame_bytes = 0;
bool running = false;
uint32_t run_id = 0;

float cal_a = 0.8059f, cal_b = 0.0f;  // mV = cal_a * code + cal_b (eFuse line fitting if available)
const char *cal_scheme = "nominal";

// ISR -> loop: one entry per conversion frame, in the order the DMA produced them.
#define TS_RING 256
volatile int64_t ts_ring[TS_RING];
volatile uint8_t drop_ring[TS_RING];
volatile uint32_t isr_done = 0;      // frames completed by the DMA
volatile uint32_t isr_overflow = 0;  // frames the driver discarded (pool full)

uint32_t frames_consumed = 0;  // frames accounted for by loop (read or known-dropped)
uint32_t frame_seq = 0;        // DATA packets emitted (never reset)
uint64_t sample_index = 0;     // rows since START, including dropped rows
uint32_t incomplete_rows = 0;
uint32_t ts_lost = 0;          // times the loop fell so far behind that the run was restarted
bool pending_drop = false;
bool first_frame = true;
uint32_t staged = 0;           // bytes of the current conversion frame collected so far

// status window
uint64_t win_index0 = 0;
int64_t win_t0 = 0;
unsigned long last_status_ms = 0;

uint8_t frame_buf[ROWS_PER_FRAME * MAX_OS * MAX_CHANNELS * SOC_ADC_DIGI_RESULT_BYTES];

// ---------- framing: CRC-16/CCITT-FALSE + COBS ----------
uint16_t crc16(const uint8_t *d, size_t n) {
  uint16_t c = 0xFFFF;
  for (size_t i = 0; i < n; i++) {
    c ^= (uint16_t)d[i] << 8;
    for (int b = 0; b < 8; b++) c = (c & 0x8000) ? (c << 1) ^ 0x1021 : (c << 1);
  }
  return c;
}

uint8_t body[320];
uint8_t wire[330];

void send_packet(uint8_t type, const uint8_t *payload, size_t n) {
  if (n + 3 > sizeof(body)) return;
  body[0] = type;
  memcpy(body + 1, payload, n);
  uint16_t c = crc16(body, n + 1);
  body[n + 1] = c & 0xFF;
  body[n + 2] = c >> 8;
  size_t len = n + 3;
  // COBS encode body -> wire, then 0x00 delimiter.
  size_t w = 1, code_at = 0;
  uint8_t code = 1;
  for (size_t i = 0; i < len; i++) {
    if (body[i] == 0) {
      wire[code_at] = code;
      code_at = w++;
      code = 1;
    } else {
      wire[w++] = body[i];
      if (++code == 0xFF) {
        wire[code_at] = code;
        code_at = w++;
        code = 1;
      }
    }
  }
  wire[code_at] = code;
  wire[w++] = 0x00;
  Serial.write(wire, w);
}

void send_text(uint8_t type, const char *text) { send_packet(type, (const uint8_t *)text, strlen(text)); }

// ---------- ISR callbacks ----------
static bool IRAM_ATTR on_conv_done(adc_continuous_handle_t, const adc_continuous_evt_data_t *, void *) {
  uint32_t k = isr_done;
  ts_ring[k % TS_RING] = esp_timer_get_time();
  drop_ring[k % TS_RING] = 0;
  isr_done = k + 1;
  return false;
}

static bool IRAM_ATTR on_pool_ovf(adc_continuous_handle_t, const adc_continuous_evt_data_t *, void *) {
  // The driver calls this right after on_conv_done for the frame it could not store.
  uint32_t k = isr_done;
  if (k) drop_ring[(k - 1) % TS_RING] = 1;
  isr_overflow++;
  return false;
}

// ---------- pin map ----------
bool is_adc1_pin(int g) {
  for (int p : ADC1_PINS)
    if (p == g) return true;
  return false;
}

// Parse "35,32,33" into out[]; returns the count, or -1 if invalid (not ADC1, duplicate, too many).
int parse_pins(const char *s, int *out) {
  int n = 0;
  while (*s) {
    while (*s == ' ' || *s == ',') s++;
    if (!*s) break;
    if (!isdigit((unsigned char)*s)) return -1;
    int g = atoi(s);
    while (isdigit((unsigned char)*s)) s++;
    if (!is_adc1_pin(g) || n >= MAX_CHANNELS) return -1;
    for (int k = 0; k < n; k++)
      if (out[k] == g) return -1;
    out[n++] = g;
  }
  return n > 0 ? n : -1;
}

void load_pins() {
  prefs.begin("emgm2", true);
  String s = prefs.getString("pins", "");
  prefs.end();
  int tmp[MAX_CHANNELS];
  int n = s.length() ? parse_pins(s.c_str(), tmp) : -1;
  if (n > 0) {
    nch = n;
    for (int k = 0; k < n; k++) pins[k] = tmp[k];
  }
}

void save_pins() {
  char s[40];
  int w = 0;
  for (int k = 0; k < nch; k++) w += snprintf(s + w, sizeof(s) - w, k ? ",%d" : "%d", pins[k]);
  prefs.begin("emgm2", false);
  prefs.putString("pins", s);
  prefs.end();
}

void pins_text(char *s, size_t len) {
  int w = 0;
  for (int k = 0; k < nch; k++) w += snprintf(s + w, len - w, k ? ",%d" : "%d", pins[k]);
}

// ---------- ADC setup ----------
void setup_calibration() {
  adc_cali_handle_t cali = NULL;
  adc_cali_line_fitting_config_t cfg = {};
  cfg.unit_id = ADC_UNIT_1;
  cfg.atten = ADC_ATTEN_DB_12;
  cfg.bitwidth = ADC_BITWIDTH_12;
  if (adc_cali_create_scheme_line_fitting(&cfg, &cali) != ESP_OK) return;
  int v1 = 0, v2 = 0;
  if (adc_cali_raw_to_voltage(cali, 1000, &v1) == ESP_OK && adc_cali_raw_to_voltage(cali, 3000, &v2) == ESP_OK) {
    cal_a = (v2 - v1) / 2000.0f;
    cal_b = v1 - cal_a * 1000.0f;
    cal_scheme = "efuse_line_fitting";
  }
  adc_cali_delete_scheme_line_fitting(cali);
}

uint32_t pick_os(uint32_t fs) {
  uint32_t o = 4;
  while (o < MAX_OS && fs * o * nch < MIN_CONV_HZ) o++;
  while (o > 1 && fs * o * nch > MAX_CONV_HZ) o--;
  return o;
}

bool adc_start() {
  os = pick_os(rate_hz);
  frame_bytes = ROWS_PER_FRAME * os * nch * SOC_ADC_DIGI_RESULT_BYTES;
  adc_continuous_handle_cfg_t hcfg = {};
  hcfg.max_store_buf_size = frame_bytes * 16;  // 16 frames = 80 ms at 2 kHz
  hcfg.conv_frame_size = frame_bytes;
  if (adc_continuous_new_handle(&hcfg, &adc) != ESP_OK) {
    adc = NULL;
    return false;
  }
  adc_digi_pattern_config_t pattern[MAX_CHANNELS] = {};
  for (int c = 0; c < nch; c++) {
    adc_unit_t unit;
    if (adc_continuous_io_to_channel(pins[c], &unit, &chan_of[c]) != ESP_OK || unit != ADC_UNIT_1) return false;
    pattern[c].atten = ADC_ATTEN_DB_12;
    pattern[c].channel = chan_of[c];
    pattern[c].unit = ADC_UNIT_1;
    pattern[c].bit_width = ADC_BITWIDTH_12;
  }
  adc_continuous_config_t ccfg = {};
  ccfg.pattern_num = nch;
  ccfg.adc_pattern = pattern;
  ccfg.sample_freq_hz = rate_hz * os * nch;
  ccfg.conv_mode = ADC_CONV_SINGLE_UNIT_1;
  ccfg.format = ADC_DIGI_OUTPUT_FORMAT_TYPE1;
  if (adc_continuous_config(adc, &ccfg) != ESP_OK) return false;

  adc_continuous_evt_cbs_t cbs = {};
  cbs.on_conv_done = on_conv_done;
  cbs.on_pool_ovf = on_pool_ovf;
  if (adc_continuous_register_event_callbacks(adc, &cbs, NULL) != ESP_OK) return false;

  isr_done = 0;
  isr_overflow = 0;
  frames_consumed = 0;
  sample_index = 0;
  incomplete_rows = 0;
  pending_drop = false;
  first_frame = true;
  staged = 0;
  win_index0 = 0;
  win_t0 = esp_timer_get_time();
  run_id++;
  if (adc_continuous_start(adc) != ESP_OK) return false;
  running = true;
  return true;
}

void adc_stop() {
  if (!adc) return;
  if (running) adc_continuous_stop(adc);
  adc_continuous_deinit(adc);
  adc = NULL;
  running = false;
}

bool restart_sampling() {
  adc_stop();
  bool ok = adc_start();
  if (!ok) {
    adc_stop();
    send_text(PKT_LOG, "level=error msg=adc_start_failed");
  }
  return ok;
}

// ---------- packets ----------
void send_hello() {
  uint8_t mac[6] = {0};
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  char built[32];
  snprintf(built, sizeof(built), "%s_%s", __DATE__, __TIME__);
  for (char *p = built; *p; p++)
    if (*p == ' ') *p = '_';  // __DATE__ contains spaces; HELLO is space-separated key=value
  char ps[40];
  pins_text(ps, sizeof(ps));
  char t[320];
  snprintf(t, sizeof(t),
           "proto=EMGM/2 fw=emg_esp32_v2/" FW_VERSION " built=%s chip=%s rev=%d mac=%02x%02x%02x%02x%02x%02x "
           "run=%lu running=%d fs=%lu os=%lu nch=%d pins=%s adc_unit=1 atten_db=12 bits=12 "
           "rows_per_frame=%d cal=%s cal_a=%.6f cal_b=%.4f baud=%d",
           built, ESP.getChipModel(), ESP.getChipRevision(), mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
           (unsigned long)run_id, running ? 1 : 0, (unsigned long)rate_hz, (unsigned long)os, nch, ps,
           ROWS_PER_FRAME, cal_scheme, cal_a, cal_b, BAUD);
  send_text(PKT_HELLO, t);
}

void send_status() {
  int64_t now = esp_timer_get_time();
  float rate = 0;
  if (now > win_t0 && sample_index > win_index0) rate = (sample_index - win_index0) * 1e6f / (float)(now - win_t0);
  win_index0 = sample_index;
  win_t0 = now;
  char t[220];
  snprintf(t, sizeof(t),
           "run=%lu running=%d rate_dev=%.2f index=%llu frames=%lu dma_overflow=%lu incomplete_rows=%lu "
           "ts_lost=%lu tx_free=%d uptime_ms=%lu",
           (unsigned long)run_id, running ? 1 : 0, rate, (unsigned long long)sample_index, (unsigned long)frame_seq,
           (unsigned long)isr_overflow, (unsigned long)incomplete_rows, (unsigned long)ts_lost,
           Serial.availableForWrite(), (unsigned long)millis());
  send_text(PKT_STATUS, t);
}

static inline void put_u32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
static inline void put_u64(uint8_t *p, uint64_t v) { memcpy(p, &v, 8); }

int channel_index(uint32_t ch) {
  for (int c = 0; c < nch; c++)
    if ((uint32_t)chan_of[c] == ch) return c;
  return -1;
}

void emit_frame(const uint8_t *buf, uint32_t got, int64_t t_us) {
  uint8_t p[24 + ROWS_PER_FRAME * MAX_CHANNELS * 2];
  uint32_t sums[ROWS_PER_FRAME][MAX_CHANNELS] = {};
  uint8_t cnt[ROWS_PER_FRAME][MAX_CHANNELS] = {};
  const uint32_t per_row = os * nch;
  uint32_t n = got / SOC_ADC_DIGI_RESULT_BYTES;
  for (uint32_t j = 0; j < n; j++) {
    adc_digi_output_data_t *d = (adc_digi_output_data_t *)&buf[j * SOC_ADC_DIGI_RESULT_BYTES];
    int c = channel_index(d->type1.channel);
    uint32_t r = j / per_row;
    if (c < 0 || r >= ROWS_PER_FRAME) continue;
    sums[r][c] += d->type1.data;
    cnt[r][c]++;
  }
  uint8_t flags = 0;
  if (first_frame) flags |= FLAG_RUN_START;
  if (pending_drop) flags |= FLAG_AFTER_DROP;
  uint8_t *q = p + 24;
  for (int r = 0; r < ROWS_PER_FRAME; r++) {
    for (int c = 0; c < nch; c++) {
      uint16_t v = (cnt[r][c] == os) ? (uint16_t)sums[r][c] : INVALID_SAMPLE;
      if (v == INVALID_SAMPLE) {
        flags |= FLAG_INCOMPLETE_ROW;
        incomplete_rows++;
      }
      memcpy(q, &v, 2);
      q += 2;
    }
  }
  put_u32(p, frame_seq);
  put_u32(p + 4, (uint32_t)sample_index);
  put_u64(p + 8, (uint64_t)t_us);
  put_u32(p + 16, isr_overflow);
  p[20] = ROWS_PER_FRAME;
  p[21] = (uint8_t)nch;
  p[22] = (uint8_t)os;
  p[23] = flags;
  send_packet(PKT_DATA, p, q - p);
  frame_seq++;
  sample_index += ROWS_PER_FRAME;
  first_frame = false;
  pending_drop = false;
}

void pump_adc() {
  if (!running) return;
  uint32_t got = 0;
  // The driver's pool is a byte ring buffer, so one read can return part of a frame. Collect exactly
  // one frame before processing it, which keeps rows aligned with conversion frames.
  while (adc_continuous_read(adc, frame_buf + staged, frame_bytes - staged, &got, 0) == ESP_OK) {
    staged += got;
    if (staged < frame_bytes) continue;
    staged = 0;
    if (isr_done - frames_consumed > TS_RING) {
      // The loop fell so far behind that frame accounting is no longer exact. Restart the run
      // rather than emit an index that might be wrong; the host sees a new run number.
      ts_lost++;
      send_text(PKT_LOG, "level=error msg=frame_accounting_lost action=restart_run");
      restart_sampling();
      send_hello();
      return;
    }
    // Frames the driver discarded (pool full) come before the oldest stored frame. Their rows still
    // advance the sample index, so the loss is visible to the host as an exact index jump.
    while (frames_consumed < isr_done && drop_ring[frames_consumed % TS_RING]) {
      sample_index += ROWS_PER_FRAME;
      frames_consumed++;
      pending_drop = true;
    }
    int64_t t_us = frames_consumed < isr_done ? ts_ring[frames_consumed % TS_RING] : -1;
    frames_consumed++;
    emit_frame(frame_buf, frame_bytes, t_us);
  }
}

// ---------- commands ----------
char cmd[64];
size_t cmd_len = 0;

void handle_command(char *c) {
  for (char *p = c; *p; p++) *p = toupper(*p);
  char ack[140];
  if (!strcmp(c, "INFO")) {
    send_hello();
    snprintf(ack, sizeof(ack), "cmd=INFO ok=1");
  } else if (!strcmp(c, "STOP")) {
    adc_stop();
    snprintf(ack, sizeof(ack), "cmd=STOP ok=1");
  } else if (!strcmp(c, "START")) {
    bool ok = restart_sampling();
    snprintf(ack, sizeof(ack), "cmd=START ok=%d run=%lu", ok, (unsigned long)run_id);
    send_text(PKT_ACK, ack);
    send_hello();
    return;
  } else if (!strncmp(c, "RATE ", 5)) {
    long hz = atol(c + 5);
    if (hz < 500 || hz > 4000) {
      snprintf(ack, sizeof(ack), "cmd=RATE ok=0 error=out_of_range min=500 max=4000");
    } else {
      rate_hz = hz;
      bool ok = restart_sampling();
      snprintf(ack, sizeof(ack), "cmd=RATE ok=%d fs=%lu os=%lu run=%lu", ok, (unsigned long)rate_hz,
               (unsigned long)os, (unsigned long)run_id);
      send_text(PKT_ACK, ack);
      send_hello();
      return;
    }
  } else if (!strncmp(c, "PINS ", 5)) {
    int tmp[MAX_CHANNELS];
    int n = parse_pins(c + 5, tmp);
    if (n < 0) {
      snprintf(ack, sizeof(ack), "cmd=PINS ok=0 error=invalid allowed=32,33,34,35,36,39 max=%d", MAX_CHANNELS);
    } else {
      nch = n;
      for (int k = 0; k < n; k++) pins[k] = tmp[k];
      save_pins();
      bool ok = restart_sampling();
      char ps[40];
      pins_text(ps, sizeof(ps));
      snprintf(ack, sizeof(ack), "cmd=PINS ok=%d pins=%s nch=%d os=%lu run=%lu", ok, ps, nch, (unsigned long)os,
               (unsigned long)run_id);
      send_text(PKT_ACK, ack);
      send_hello();
      return;
    }
  } else {
    snprintf(ack, sizeof(ack), "cmd=%.20s ok=0 error=unknown_command", c);
  }
  send_text(PKT_ACK, ack);
}

void read_commands() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      cmd[cmd_len] = 0;
      if (cmd_len) handle_command(cmd);
      cmd_len = 0;
    } else if (cmd_len < sizeof(cmd) - 1) {
      cmd[cmd_len++] = ch;
    }
  }
}

void setup() {
  Serial.setTxBufferSize(8192);
  Serial.begin(BAUD);
  delay(200);
  load_pins();
  setup_calibration();
  if (!adc_start()) {
    adc_stop();
    send_text(PKT_LOG, "level=error msg=adc_init_failed");
  }
  send_hello();
  last_status_ms = millis();
}

void loop() {
  read_commands();
  pump_adc();
  unsigned long now = millis();
  if (now - last_status_ms >= STATUS_PERIOD_MS) {
    last_status_ms = now;
    send_status();
  }
}
