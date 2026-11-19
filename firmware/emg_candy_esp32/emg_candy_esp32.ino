// emg_candy_esp32: 3x Muscle BioAmp Candy on an ESP32 DevKit, tuned for signal quality.
//
// Wiring (all ADC1 pins, so Wi-Fi/BT never blocks the ADC):
//   Candy 1 OUT -> D35   Candy 2 OUT -> D32   Candy 3 OUT -> D33
//   Every Candy: VCC -> 3V3 rail, GND -> GND rail. Never VIN.
// SAFETY: laptop on battery (charger unplugged) while electrodes are on the skin.
//
// Output is the same text format as firmware/emg_stream, so `emgmirror record`
// and the dashboard read it unchanged:
//   "# emgmirror fs=2000 ch=3 bits=12 ..."  then one "a,b,c" line per sample.
//
// Quality choices vs. the plain analogRead() loop:
//  1. Hardware-timed sampling (ESP-IDF ADC continuous/DMA driver). Sample spacing is
//     set by the ADC clock, not by when Serial is free, so there is no timing jitter.
//     A 4 KB DMA pool (~85 ms) absorbs Serial stalls.
//  2. 4x oversampling: each pin is converted at 8 kHz and 4 conversions are averaged
//     into one 2 kHz sample. Halves the ESP32 ADC's white noise and puts a null at
//     2 kHz, which suppresses content that would otherwise alias down (the Candy's
//     own ~720 Hz low-pass is gentle).
//  3. eFuse-calibrated (line-fitting) millivolts, rescaled to 12-bit counts
//     (counts = mV * 4095 / 3300) with sub-count precision kept from the averaging.
//     Makes amplitudes comparable across channels and across left/right sessions.
//     With the config's adc_bits=12 and adc_vref=3.3, counts convert back to volts.
//  4. Every 10 s the header repeats with "rate=" (rows/s actually sent) and
//     "drop=" (DMA overflows since boot) so data-quality problems are visible.

#include <esp_adc/adc_continuous.h>
#include <esp_adc/adc_cali.h>
#include <esp_adc/adc_cali_scheme.h>

#define NUM_CHANNELS 3
#define SAMPLE_RATE_HZ 2000   // output rate per channel
#define OVERSAMPLE 4          // conversions averaged per output sample
#define BAUD 500000           // ~30 kB/s of text = ~60% of the link
#define ROWS_PER_FRAME 10     // DMA frame = 10 output rows (5 ms)

const int PINS[NUM_CHANNELS] = {35, 32, 33};
const int ADC_BITS = 12;

const uint32_t CONV_RATE = SAMPLE_RATE_HZ * OVERSAMPLE * NUM_CHANNELS;               // 24 kHz
const uint32_t FRAME_BYTES = ROWS_PER_FRAME * OVERSAMPLE * NUM_CHANNELS * SOC_ADC_DIGI_RESULT_BYTES;

adc_continuous_handle_t adc = NULL;
adc_channel_t chan_of[NUM_CHANNELS];
float mv_a = 0.8059f, mv_b = 0.0f;   // mV = a*raw + b; replaced by eFuse calibration
bool calibrated = false;

volatile uint32_t pool_overflows = 0;
uint32_t sums[NUM_CHANNELS], counts[NUM_CHANNELS];
uint32_t rows_window = 0;
unsigned long window_ms = 0;
uint8_t frame[FRAME_BYTES];

static bool IRAM_ATTR on_overflow(adc_continuous_handle_t, const adc_continuous_evt_data_t *, void *) {
  pool_overflows++;
  return false;
}

void header(float rate) {
  Serial.printf("# emgmirror fs=%d ch=%d bits=%d os=%d cal=%s rate=%.0f drop=%lu\n",
                SAMPLE_RATE_HZ, NUM_CHANNELS, ADC_BITS, OVERSAMPLE, calibrated ? "efuse" : "nominal",
                rate, (unsigned long)pool_overflows);
}

void setup_calibration() {
  adc_cali_handle_t cali = NULL;
  adc_cali_line_fitting_config_t cfg = {};
  cfg.unit_id = ADC_UNIT_1;
  cfg.atten = ADC_ATTEN_DB_12;
  cfg.bitwidth = ADC_BITWIDTH_12;
  if (adc_cali_create_scheme_line_fitting(&cfg, &cali) != ESP_OK) return;
  // Line fitting is linear: recover slope/offset once, then apply in float so the
  // sub-LSB precision gained by averaging survives.
  int v1 = 0, v2 = 0;
  if (adc_cali_raw_to_voltage(cali, 1000, &v1) == ESP_OK && adc_cali_raw_to_voltage(cali, 3000, &v2) == ESP_OK) {
    mv_a = (v2 - v1) / 2000.0f;
    mv_b = v1 - mv_a * 1000.0f;
    calibrated = true;
  }
  adc_cali_delete_scheme_line_fitting(cali);
}

bool setup_adc() {
  adc_continuous_handle_cfg_t hcfg = {};
  hcfg.max_store_buf_size = 4096;
  hcfg.conv_frame_size = FRAME_BYTES;
  if (adc_continuous_new_handle(&hcfg, &adc) != ESP_OK) return false;

  adc_digi_pattern_config_t pattern[NUM_CHANNELS] = {};
  for (int c = 0; c < NUM_CHANNELS; c++) {
    adc_unit_t unit;
    if (adc_continuous_io_to_channel(PINS[c], &unit, &chan_of[c]) != ESP_OK || unit != ADC_UNIT_1) return false;
    pattern[c].atten = ADC_ATTEN_DB_12;  // ~0.15..3.1 V usable
    pattern[c].channel = chan_of[c];
    pattern[c].unit = ADC_UNIT_1;
    pattern[c].bit_width = ADC_BITWIDTH_12;
  }
  adc_continuous_config_t ccfg = {};
  ccfg.pattern_num = NUM_CHANNELS;
  ccfg.adc_pattern = pattern;
  ccfg.sample_freq_hz = CONV_RATE;
  ccfg.conv_mode = ADC_CONV_SINGLE_UNIT_1;
  ccfg.format = ADC_DIGI_OUTPUT_FORMAT_TYPE1;
  if (adc_continuous_config(adc, &ccfg) != ESP_OK) return false;

  adc_continuous_evt_cbs_t cbs = {};
  cbs.on_pool_ovf = on_overflow;
  adc_continuous_register_event_callbacks(adc, &cbs, NULL);
  return adc_continuous_start(adc) == ESP_OK;
}

void setup() {
  Serial.setTxBufferSize(8192);
  Serial.begin(BAUD);
  delay(200);
  setup_calibration();
  if (!setup_adc()) {
    while (true) {
      Serial.println("# emgmirror error=adc_init_failed");
      delay(1000);
    }
  }
  header(0);
  window_ms = millis();
}

static inline int channel_index(uint32_t ch) {
  for (int c = 0; c < NUM_CHANNELS; c++)
    if (chan_of[c] == ch) return c;
  return -1;
}

void emit_row() {
  char line[40];
  int n = 0;
  for (int c = 0; c < NUM_CHANNELS; c++) {
    float raw = (float)sums[c] / counts[c];
    float counts12 = (mv_a * raw + mv_b) * (4095.0f / 3300.0f);
    int v = (int)lroundf(counts12);
    v = v < 0 ? 0 : (v > 4095 ? 4095 : v);
    if (c) line[n++] = ',';
    n += snprintf(line + n, sizeof(line) - n, "%d", v);
    sums[c] = 0;
    counts[c] = 0;
  }
  line[n++] = '\n';
  Serial.write((const uint8_t *)line, n);
  rows_window++;
}

void loop() {
  uint32_t got = 0;
  while (adc_continuous_read(adc, frame, FRAME_BYTES, &got, 0) == ESP_OK) {
    for (uint32_t i = 0; i + SOC_ADC_DIGI_RESULT_BYTES <= got; i += SOC_ADC_DIGI_RESULT_BYTES) {
      adc_digi_output_data_t *p = (adc_digi_output_data_t *)&frame[i];
      int c = channel_index(p->type1.channel);
      if (c < 0) continue;
      sums[c] += p->type1.data;
      counts[c]++;
      // Emit once every channel has its OVERSAMPLE conversions. The pattern is
      // round-robin, so the last channel completing closes the row.
      if (c == NUM_CHANNELS - 1 && counts[c] >= OVERSAMPLE) {
        bool full = true;
        for (int k = 0; k < NUM_CHANNELS; k++) full &= counts[k] > 0;
        if (full) emit_row();
        else for (int k = 0; k < NUM_CHANNELS; k++) { sums[k] = 0; counts[k] = 0; }
      }
    }
  }

  unsigned long now = millis();
  if (now - window_ms >= 10000UL) {
    header(rows_window * 1000.0f / (now - window_ms));
    rows_window = 0;
    window_ms = now;
  }
}
