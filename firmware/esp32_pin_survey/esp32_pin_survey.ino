// Structured raw sensor-signal logger for classic ESP32-D0WD-V3.
// Emits CSV only at 115200 baud. Columns are named by GPIO because the current
// physical sensor-to-pin mapping is unknown. GPIO1/3 carry USB serial; 6-11
// are flash pins; GPIO34-39 are input-only (and are sampled only as inputs).

const int adcPins[] = {0,2,4,12,13,14,15,25,26,27,32,33,34,35,36,39};
const int adcCount = sizeof(adcPins)/sizeof(adcPins[0]);
const int digitalPins[] = {0,2,4,5,12,13,14,15,16,17,18,19,21,22,23,25,26,27,32,33,34,35,36,39};
const int digitalCount = sizeof(digitalPins)/sizeof(digitalPins[0]);
const int touchPins[] = {0,2,4,12,13,14,15,27,32,33};
const int touchCount = sizeof(touchPins)/sizeof(touchPins[0]);
const uint32_t ROW_PERIOD_US = 50000; // nominal 20 rows per second

void printHeader() {
  Serial.print("elapsed_ms");
  for (int i=0;i<adcCount;i++) { Serial.printf(",gpio%d_adc_raw,gpio%d_mV",adcPins[i],adcPins[i]); }
  for (int i=0;i<digitalCount;i++) Serial.printf(",gpio%d_digital",digitalPins[i]);
  for (int i=0;i<touchCount;i++) Serial.printf(",touch_gpio%d",touchPins[i]);
  Serial.println();
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
  for (int i=0;i<digitalCount;i++) pinMode(digitalPins[i], INPUT);
  delay(300);
  printHeader();
}

void loop() {
  const uint32_t started = micros();
  Serial.print(millis());
  for (int i=0;i<adcCount;i++) {
    const int raw = analogRead(adcPins[i]);
    const int mv = analogReadMilliVolts(adcPins[i]);
    Serial.printf(",%d,%d",raw,mv);
  }
  for (int i=0;i<digitalCount;i++) Serial.printf(",%d",digitalRead(digitalPins[i]));
  for (int i=0;i<touchCount;i++) Serial.printf(",%u",(unsigned)touchRead(touchPins[i]));
  Serial.println();
  while ((uint32_t)(micros()-started) < ROW_PERIOD_US) delayMicroseconds(100);
}
