#include <WiFi.h>
#include <WebServer.h>
#include <driver/i2s.h>
#include <driver/adc.h>
#include "dashboard.h"

// Classic ESP32 analog microphone module: AO -> GPIO34 (ADC1_CH6).
static const int MIC_PIN = 34;
static const char *AP_SSID = "ESP32-Mic";
static const char *AP_PASS = "voicecapture";
static const uint32_t SAMPLE_RATE = 8000;
static const size_t RING_SIZE = 65536; // 8 seconds at 8-bit / 8 kHz
static const size_t DMA_SAMPLES = 512;
static uint8_t audioRing[RING_SIZE];
static volatile uint32_t ringHead = 0, ringTail = 0, droppedSamples = 0;
static volatile bool recording = false;
static WebServer server(80);

static void enqueueSample(uint8_t sample) {
  uint32_t next = (ringHead + 1) % RING_SIZE;
  if (next == ringTail) { droppedSamples++; return; }
  audioRing[ringHead] = sample;
  ringHead = next;
}

// Drain the I2S ADC's DMA buffers into a byte ring. This runs between HTTP requests.
static void pumpAudio() {
  if (!recording) return;
  uint16_t raw[DMA_SAMPLES];
  for (int pass = 0; pass < 4; ++pass) {
    size_t got = 0;
    esp_err_t e = i2s_read(I2S_NUM_0, raw, sizeof(raw), &got, 0);
    if (e != ESP_OK || got < sizeof(uint16_t)) break;
    size_t count = got / sizeof(uint16_t);
    for (size_t i = 0; i < count; ++i) {
      uint16_t adc = raw[i] & 0x0FFF; // ESP32 I2S ADC samples carry 12-bit ADC data
      enqueueSample((uint8_t)(adc >> 4));
    }
  }
}

static bool initI2s() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_ADC_BUILT_IN);
  cfg.sample_rate = SAMPLE_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 256;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = false;
  cfg.fixed_mclk = 0;
  if (i2s_driver_install(I2S_NUM_0, &cfg, 0, nullptr) != ESP_OK) return false;
  if (i2s_set_adc_mode(ADC_UNIT_1, ADC1_CHANNEL_6) != ESP_OK) return false;
  return true;
}

static void handleRoot() { server.send_P(200, "text/html; charset=utf-8", DASHBOARD_HTML); }
static void handleStatus() {
  char json[192];
  uint32_t used = (ringHead + RING_SIZE - ringTail) % RING_SIZE;
  snprintf(json, sizeof(json), "{\"recording\":%s,\"rate\":%lu,\"queued\":%lu,\"drops\":%lu,\"ip\":\"%s\"}",
    recording ? "true" : "false", (unsigned long)SAMPLE_RATE, (unsigned long)used,
    (unsigned long)droppedSamples, WiFi.softAPIP().toString().c_str());
  server.send(200, "application/json", json);
}
static void handleStart() {
  if (recording) { server.send(409, "text/plain", "Already recording"); return; }
  ringHead = ringTail = droppedSamples = 0;
  i2s_zero_dma_buffer(I2S_NUM_0);
  if (i2s_adc_enable(I2S_NUM_0) != ESP_OK) { server.send(500, "text/plain", "ADC start failed"); return; }
  recording = true;
  server.send(200, "text/plain", "recording");
}
static void handleStop() {
  if (recording) {
    pumpAudio();
    recording = false;
    i2s_adc_disable(I2S_NUM_0);
  }
  server.send(200, "text/plain", "stopped");
}
static void handleAudio() {
  pumpAudio();
  uint8_t packet[2048];
  size_t count = 0;
  while (count < sizeof(packet) && ringTail != ringHead) {
    packet[count++] = audioRing[ringTail];
    ringTail = (ringTail + 1) % RING_SIZE;
  }
  if (!count) { server.send(204, "application/octet-stream", ""); return; }
  server.setContentLength(count);
  server.send(200, "application/octet-stream", "");
  server.sendContent((const char *)packet, count);
}
static void handleNotFound() { server.send(404, "text/plain", "Not found"); }

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  analogSetPinAttenuation(MIC_PIN, ADC_11db);
  if (!initI2s()) Serial.println("I2S ADC initialization failed");
  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASS, 1, false, 2);
  server.on("/", HTTP_GET, handleRoot);
  server.on("/status", HTTP_GET, handleStatus);
  server.on("/start", HTTP_POST, handleStart);
  server.on("/stop", HTTP_POST, handleStop);
  server.on("/audio", HTTP_GET, handleAudio);
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.printf("Mic dashboard: http://%s/\n", WiFi.softAPIP().toString().c_str());
  Serial.printf("Wi-Fi: %s\n", AP_SSID);
}
void loop() {
  pumpAudio();
  server.handleClient();
  delay(0);
}
