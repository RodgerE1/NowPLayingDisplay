/*************************************************************

ESP32 | PC "Title – Artist – Status" LCD

v4.5.0 - Backlight driven by status field
*************************************************************/

#include <LiquidCrystal_I2C.h>

/* ---------- LCD + Backlight ---------- */
// NOTE: On a standard ESP32, the default I2C pins are SDA=21, SCL=22
#define LCD_ADDR 0x27
#define BACKLIGHT_PIN 18
LiquidCrystal_I2C lcd(LCD_ADDR, 16, 2);

/* ---------- GLOBAL STATE ---------- */
String lastDisplay;
String currentDisplay;
int currentStatus = -1;

/* ---------- PROTOTYPES ---------- */
void updateBacklight(int status);
void wrapTwoLines(const String &s);

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);

  // If you are NOT using the default pins (21, 22), you must specify them here:
  // Wire.begin(SDA_PIN, SCL_PIN);

  pinMode(BACKLIGHT_PIN, OUTPUT);
  lcd.begin();
  lcd.backlight();

  lcd.setCursor(0, 0);
  lcd.print(F("PC Now Playing"));
  lcd.setCursor(0, 1);
  lcd.print(F("Waiting for PC..."));
}

void loop() {
  // 1) Read incoming line
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();

    // 1a) Special CLEAR command
    if (line.equalsIgnoreCase("CLEAR")) {
      lcd.clear();
      currentDisplay = "";
      updateBacklight(0);    // treat CLEAR as "stop"
    }
    else {
      // 1b) Expect TITLE|ARTIST|STATUS
      int p1 = line.indexOf('|');
      int p2 = line.indexOf('|', p1 + 1);
      if (p1 > 0 && p2 > p1) {
        String title  = line.substring(0, p1);
        String artist = line.substring(p1 + 1, p2);
        String stStr  = line.substring(p2 + 1);
        int    st     = stStr.toInt();

        currentDisplay = title + " - " + artist;
        currentStatus  = st;

        // 2) Drive backlight immediately based on status
        updateBacklight(currentStatus);
      }
    }
  }

  // 3) Only redraw LCD if text changed
  if (currentDisplay != lastDisplay) {
    wrapTwoLines(currentDisplay);
    lastDisplay = currentDisplay;
  }
}

// Turn the backlight ON if Playing (4), else OFF
void updateBacklight(int status) {
  if (status == 4) {
    digitalWrite(BACKLIGHT_PIN, HIGH);
    lcd.backlight();
    Serial.println(F("STATE: Playing → Backlight ON"));
  } else {
    digitalWrite(BACKLIGHT_PIN, LOW);
    lcd.noBacklight();
    Serial.printf("STATE: Status %d → Backlight OFF\n", status);
  }
}

// Utility to split up to two lines on a 16×2 LCD
void wrapTwoLines(const String &sIn) {
  String s = sIn;
  lcd.clear();

  if (s.length() <= 16) {
    lcd.setCursor(0, 0);
    lcd.print(s);
    return;
  }

  int cut = s.lastIndexOf(' ', 15);
  if (cut <= 0 || cut > 16) cut = 16;
  String r1 = s.substring(0, cut);
  String r2 = s.substring(cut);
  r2.trim();
  if (r2.length() > 16) r2 = r2.substring(0, 15) + '~';

  lcd.setCursor(0, 0); lcd.print(r1);
  lcd.setCursor(0, 1); lcd.print(r2);
}