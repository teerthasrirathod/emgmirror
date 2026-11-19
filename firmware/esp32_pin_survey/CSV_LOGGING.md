# ESP32 raw sensor CSV logger

The ESP32-D0WD-V3 emits a CSV header followed by numeric rows over USB serial at 115200 baud. The sample interval is 50 ms (nominally 20 rows/s). Sensor identities are not hardcoded: columns use GPIO names so no stale sensor label is presented as fact.

## Dashboard

Open `sensor_csv_dashboard.html` in Brave, Chrome, or Edge. Connect the ESP32 serial port, wait for the 67-column header, then click **Start CSV** and **Stop & save**. The dashboard streams live readings and downloads the captured CSV when stopped. The current Brave file-page fallback keeps the capture in browser memory until Stop, then saves it as a CSV.

The currently tested capture is in the user Downloads folder as `esp32-sensors-2026-11-14T14-12-14.csv`; it contains one header and eight numeric rows, all 67 fields wide.

## CSV fields

- `elapsed_ms`: milliseconds since ESP32 boot.
- `gpioN_adc_raw`, `gpioN_mV`: raw 12-bit ADC count and calibrated millivolts for ADC-capable pins 0, 2, 4, 12–15, 25–27, and 32–39.
- `gpioN_digital`: digital input state for usable GPIOs.
- `touch_gpioN`: ESP32 capacitive-touch peripheral readings on touch-capable GPIOs.

GPIO1/3 are left to USB serial, GPIO6–11 are ESP32 flash pins, and GPIO34–39 are input-only. Channel names are pin-based until the physical sensor-to-pin mapping is established.

## Firmware backup

Before replacing the ESP32 firmware, the entire 4 MB flash was read back to `../firmware_backups/esp32_flash_before_structured_csv_2026-11-14.bin`.

SHA-256: `C197D695068A7D6C0ECB033DBF79D860CE118099FC4AA6CB0ED6D5C4857E3BEF`

The original pre-CSV pin-survey source is saved as `../firmware_backups/esp32_pin_survey_before_structured_csv.ino`.
