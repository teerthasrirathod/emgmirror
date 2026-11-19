// esp32_rig_check: wiring check for the forearm rig on an ESP32 DevKit.
// Prints one CSV row every 50 ms. Open Serial Monitor at 115200 baud.
// Everything is powered from 3V3 (never VIN).
//   VP  (GPIO36) <- KY-038 small mic AO   (yellow)
//   VN  (GPIO39) <- KY-037 big mic AO     (green)
//   D34          <- KY-024 magnet AO      (blue)
//   D35          <- Muscle BioAmp Candy OUT (orange)
// Tap each sensor and watch which column moves.

const int MIC38 = 36, MIC37 = 39, HALL = 34, EMG = 35;
int hallRest = 0;
float env = 0;

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);        // 0..4095
  analogSetAttenuation(ADC_11db);  // full 0..3.3 V range
  delay(300);
  hallRest = analogRead(HALL);
  Serial.println("mic38,mic37,bulge,emg_raw,emg_env");
}

void loop() {
  int m38 = analogRead(MIC38);
  int m37 = analogRead(MIC37);
  int bulge = analogRead(HALL) - hallRest;
  int raw = analogRead(EMG);
  env = 0.8 * env + 0.2 * abs(raw - 2048);
  Serial.printf("%d,%d,%d,%d,%d\n", m38, m37, bulge, raw, (int)env);
  delay(50);
}
