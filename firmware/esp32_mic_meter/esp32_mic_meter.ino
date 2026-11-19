// Microphone amplitude meter for the microphone analog output on ESP32 GPIO39 (VN / ADC1_CH3).
// Reports windowed AC RMS and peak-to-peak voltage. These are electrical signal levels,
// not calibrated dB SPL. GPIO39 is input-only, so it is never driven.
const int MIC_PIN = 39;
const uint32_t WINDOW_US = 100000; // 100 ms windows => 10 display updates/second
void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  analogSetPinAttenuation(MIC_PIN, ADC_11db);
  delay(300);
  Serial.println("elapsed_ms,mic_gpio39_mean_V,mic_gpio39_ac_rms_mV,mic_gpio39_peak_to_peak_mV,samples,sample_rate_Hz,clipped_samples");
}
void loop() {
  uint64_t sum = 0, sumSquares = 0;
  uint32_t count = 0, clipped = 0;
  int minRaw = 4095, maxRaw = 0; uint32_t minMv = 3300, maxMv = 0;
  const uint32_t startUs = micros();
  while ((uint32_t)(micros() - startUs) < WINDOW_US) {
    const int raw = analogRead(MIC_PIN);
    const uint32_t mv = analogReadMilliVolts(MIC_PIN);
    sum += mv;
    sumSquares += (uint64_t)mv * (uint64_t)mv;
    if (raw < minRaw) minRaw = raw;
    if (raw > maxRaw) maxRaw = raw;
    if (raw <= 2 || raw >= 4093) clipped++;
    if (mv < minMv) minMv = mv;
    if (mv > maxMv) maxMv = mv;
    count++;
  }
  if (!count) return;
  const double mean = (double)sum / count;
  const double variance = max(0.0, (double)sumSquares / count - mean * mean);
  const float meanV = (float)(mean / 1000.0);
  const float rmsMv = (float)sqrt(variance);
  const float peakToPeakMv = (float)(maxMv - minMv);
  const float actualRate = count * 1000000.0f / (uint32_t)(micros() - startUs);
  Serial.printf("%lu,%.3f,%.2f,%.2f,%lu,%.0f,%lu\n", (unsigned long)millis(), meanV, rmsMv, peakToPeakMv, (unsigned long)count, actualRate, (unsigned long)clipped);
}
