// emg_stream: sample N analog EMG channels at a fixed rate and stream them
// over USB serial as comma-separated ADC counts, one sample per line.
//
// Works with any analog-output EMG front-end (e.g. BioAmp EXG Pill, MyoWare
// 2.0 RAW/"SIG" output, AD8232-style boards) on Arduino Uno/Nano/Mega or ESP32.
// Use the RAW (unrectified) output, not an on-board envelope output.
//
// Output:  "# emgmirror fs=1000 ch=4 bits=10"   header, repeated every 10 s
//          "512,498,530,507"                    one line per sample
//
// Match fs, channel count, adc_bits and serial_baud in configs/*.yaml.
//
// SAFETY: power the board from a battery-powered laptop (charger unplugged)
// or a USB isolator while electrodes are on the skin.

#ifndef NUM_CHANNELS
#define NUM_CHANNELS 4          // 2 for sequential mode, 4 for mirror mode
#endif
#ifndef SAMPLE_RATE_HZ
#define SAMPLE_RATE_HZ 1000     // Uno can do 1000 Hz for up to ~4 channels
#endif
#define BAUD 500000

#if defined(ESP32)
const int PINS[] = {36, 39, 34, 35, 32, 33};
const int ADC_BITS = 12;
#else
const int PINS[] = {A0, A1, A2, A3, A4, A5};
const int ADC_BITS = 10;
#endif

const unsigned long PERIOD_US = 1000000UL / SAMPLE_RATE_HZ;
unsigned long next_us;
unsigned long last_header_ms = 0;

void header() {
  Serial.print("# emgmirror fs=");
  Serial.print(SAMPLE_RATE_HZ);
  Serial.print(" ch=");
  Serial.print(NUM_CHANNELS);
  Serial.print(" bits=");
  Serial.println(ADC_BITS);
}

void setup() {
  Serial.begin(BAUD);
#if defined(ESP32)
  analogReadResolution(ADC_BITS);
#endif
  delay(200);
  header();
  next_us = micros();
}

void loop() {
  // Fixed-rate sampling against micros() so timing does not drift with Serial.
  if ((long)(micros() - next_us) < 0) return;
  next_us += PERIOD_US;

  for (int c = 0; c < NUM_CHANNELS; c++) {
    if (c) Serial.write(',');
    Serial.print(analogRead(PINS[c]));
  }
  Serial.write('\n');

  if (millis() - last_header_ms > 10000UL) {
    last_header_ms = millis();
    header();
  }
}
