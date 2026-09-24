/*
  Room_PC_Display.ino

  Main firmware for the ESP32-2432S028R "Cheap Yellow Display".

  Features:
    - 320 x 240 landscape UI; no touch input
    - DHT22 / AM2302 room temperature and humidity on GPIO 27
    - automatic System Monitor and Now Playing screens
    - CPU Package, GPU, selected system fans, three SSD temperatures,
      and Windows media data received from pc_sender.py
    - Spotify album art supplied by the Windows companion and drawn directly
      to the TFT in full RGB565 color when available
    - automatic UDP discovery so the Windows companion normally needs no IP
    - no cloud logging and no external-site uploads

  Board in Arduino IDE:
    ESP32 Arduino -> ESP32 Dev Module

  Required Arduino libraries:
    DHT sensor library by Adafruit
    Adafruit Unified Sensor
    TFT_eSPI by Bodmer, configured with the CYD User_Setup.h
*/

#include <WiFi.h>
#include <WebServer.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include <DHT.h>
#include <TFT_eSPI.h>
#include <math.h>

#include "secrets.h"

// ---------------------------- Hardware ----------------------------

constexpr uint8_t DHT_PIN = 27;
constexpr uint8_t DHT_TYPE = DHT22;
constexpr uint8_t BACKLIGHT_PIN = 21;

constexpr int SCREEN_WIDTH = 320;
constexpr int SCREEN_HEIGHT = 240;
constexpr int ALBUM_ART_WIDTH = 128;
constexpr int ALBUM_ART_HEIGHT = 128;
constexpr int SYSTEM_FAN_COUNT = 7;
constexpr int GPU_FAN_COUNT = 2;
constexpr int STORAGE_TEMPERATURE_COUNT = 3;
constexpr size_t ALBUM_ART_BYTE_COUNT =
    ALBUM_ART_WIDTH * ALBUM_ART_HEIGHT * sizeof(uint16_t);

DHT dht(DHT_PIN, DHT_TYPE);
TFT_eSPI tft = TFT_eSPI();
TFT_eSprite canvas = TFT_eSprite(&tft);

// ----------------------------- Network -----------------------------

WebServer webServer(80);
WiFiUDP discoveryUdp;

constexpr uint16_t DISCOVERY_PORT = 4210;
const char DISCOVERY_REQUEST[] = "CYD_ROOM_DISPLAY_DISCOVER_V1";
const char DISCOVERY_REPLY[] = "CYD_ROOM_DISPLAY_V1";

bool networkServicesStarted = false;
unsigned long lastWiFiAttemptMs = 0;
constexpr unsigned long WIFI_RETRY_INTERVAL_MS = 10000UL;

// ------------------------------ Timing -----------------------------

constexpr unsigned long SENSOR_INTERVAL_MS = 2500UL;
constexpr unsigned long PC_OFFLINE_TIMEOUT_MS = 15000UL;
constexpr unsigned long DRAW_INTERVAL_MS = 1000UL;

unsigned long lastSensorReadMs = 0;
unsigned long lastPcUpdateMs = 0;
unsigned long lastDrawMs = 0;

// ------------------------------- Data ------------------------------

float roomTempC = NAN;
float roomTempF = NAN;
float roomHumidity = NAN;
float cpuTempC = NAN;
float gpuTempC = NAN;
int systemFanRpm[SYSTEM_FAN_COUNT] = {-1, -1, -1, -1, -1, -1, -1};
int gpuFanRpm[GPU_FAN_COUNT] = {-1, -1};
float storageTempC[STORAGE_TEMPERATURE_COUNT] = {NAN, NAN, NAN};
String storageName[STORAGE_TEMPERATURE_COUNT] = {
    "SSD 1", "SSD 2", "SSD 3"};

String mediaTitle = "Waiting for PC";
String mediaArtist = "Connect the Windows companion";
String mediaSource = "PC OFFLINE";

bool mediaPlaying = false;
bool pcHasConnected = false;
bool pcOnline = false;

long mediaPositionSeconds = 0;
long mediaDurationSeconds = 0;

uint16_t spotifyAlbumArt[ALBUM_ART_WIDTH * ALBUM_ART_HEIGHT];
size_t albumArtUploadBytes = 0;
bool albumArtUploadAuthorized = false;
bool albumArtUploadValid = false;
bool spotifyAlbumArtAvailable = false;
bool spotifyArtworkVisibleOnTft = false;
bool spotifyArtworkNeedsRefresh = false;

bool displayDirty = true;

// ------------------------------ Colors -----------------------------

constexpr uint16_t rgb565(uint8_t red, uint8_t green, uint8_t blue) {
  return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3);
}

constexpr uint16_t COLOR_BG = rgb565(5, 8, 12);
constexpr uint16_t COLOR_PANEL = rgb565(17, 25, 35);
constexpr uint16_t COLOR_MEDIA_LEFT = rgb565(21, 40, 57);
constexpr uint16_t COLOR_LINE = rgb565(41, 64, 82);
constexpr uint16_t COLOR_TEXT = rgb565(242, 248, 252);
constexpr uint16_t COLOR_MUTED = rgb565(158, 179, 194);
constexpr uint16_t COLOR_CYAN = rgb565(84, 230, 223);
constexpr uint16_t COLOR_AMBER = rgb565(255, 193, 90);
constexpr uint16_t COLOR_TILE_BLUE = rgb565(31, 78, 112);
constexpr uint16_t COLOR_TILE_PURPLE = rgb565(111, 61, 118);
constexpr uint16_t COLOR_ERROR = rgb565(255, 105, 105);
// This color is used only as a transparency key while an album cover is
// already visible on the TFT. It is deliberately absent from the UI palette.
constexpr uint16_t COLOR_ARTWORK_TRANSPARENT = rgb565(255, 0, 255);

// -------------------------- String helpers -------------------------

String limitedText(String value, size_t maximumLength) {
  value.trim();
  value.replace("\r", " ");
  value.replace("\n", " ");

  while (value.indexOf("  ") >= 0) {
    value.replace("  ", " ");
  }

  if (value.length() > maximumLength) {
    value.remove(maximumLength);
  }
  return value;
}

String upperText(String value) {
  value.toUpperCase();
  return value;
}

String shortenToWidth(String value, int maximumWidth, uint8_t font) {
  if (canvas.textWidth(value, font) <= maximumWidth) {
    return value;
  }

  const String ellipsis = "...";
  while (value.length() > 0 &&
         canvas.textWidth(value + ellipsis, font) > maximumWidth) {
    value.remove(value.length() - 1);
  }
  return value + ellipsis;
}

String secondsToClock(long totalSeconds) {
  if (totalSeconds < 0) totalSeconds = 0;

  const long hours = totalSeconds / 3600;
  const int minutes = (totalSeconds % 3600) / 60;
  const int seconds = totalSeconds % 60;

  char buffer[16];
  if (hours > 0) {
    snprintf(buffer, sizeof(buffer), "%ld:%02d:%02d", hours, minutes, seconds);
  } else {
    snprintf(buffer, sizeof(buffer), "%d:%02d", minutes, seconds);
  }
  return String(buffer);
}

String nullableTemperature(float value) {
  if (isnan(value)) return "--";
  return String(value, 0);
}

float parseTemperatureArgument(const String &name) {
  if (!webServer.hasArg(name)) return NAN;

  String value = webServer.arg(name);
  value.trim();
  if (value.length() == 0) return NAN;

  const float parsed = value.toFloat();
  if (parsed < -30.0F || parsed > 150.0F) return NAN;
  return parsed;
}

int parseRpmArgument(const String &name) {
  if (!webServer.hasArg(name)) return -1;

  String value = webServer.arg(name);
  value.trim();
  if (value.length() == 0) return -1;

  for (size_t index = 0; index < value.length(); index++) {
    if (value[index] < '0' || value[index] > '9') return -1;
  }

  const long parsed = value.toInt();
  if (parsed < 0 || parsed > 50000) return -1;
  return static_cast<int>(parsed);
}

String nullableRpm(int value) {
  return value < 0 ? "--" : String(value);
}

// ---------------------------- UI drawing ---------------------------

void drawWiFiIcon(int x, int y, uint16_t color) {
  canvas.drawArc(x + 8, y + 9, 8, 7, 220, 320, color, COLOR_BG);
  canvas.drawArc(x + 8, y + 10, 5, 4, 220, 320, color, COLOR_BG);
  canvas.fillCircle(x + 8, y + 12, 1, color);
}

void drawMusicTile() {
  canvas.fillRoundRect(16, 48, ALBUM_ART_WIDTH, ALBUM_ART_HEIGHT, 8,
                       COLOR_TILE_BLUE);
  canvas.fillRoundRect(80, 48, 64, ALBUM_ART_HEIGHT, 8, COLOR_TILE_PURPLE);
  canvas.fillRect(80, 48, 56, ALBUM_ART_HEIGHT, COLOR_TILE_PURPLE);

  canvas.drawLine(76, 73, 76, 138, COLOR_TEXT);
  canvas.drawLine(77, 73, 77, 138, COLOR_TEXT);
  canvas.drawLine(76, 73, 104, 80, COLOR_TEXT);
  canvas.drawLine(76, 76, 104, 83, COLOR_TEXT);
  canvas.drawLine(104, 80, 104, 130, COLOR_TEXT);
  canvas.drawLine(105, 80, 105, 130, COLOR_TEXT);
  canvas.fillCircle(67, 143, 9, COLOR_TEXT);
  canvas.fillCircle(95, 135, 9, COLOR_TEXT);
}

bool shouldDrawSpotifyArtwork() {
  return pcOnline && mediaPlaying && spotifyAlbumArtAvailable &&
         mediaSource == "Spotify";
}

void drawMediaArtwork() {
  // Spotify artwork is layered directly onto the TFT after the 8-bit UI
  // sprite is pushed. Sending it through the sprite would quantize its colors.
  if (shouldDrawSpotifyArtwork()) return;

  drawMusicTile();
}

void drawFullColorSpotifyArtwork() {
  if (!shouldDrawSpotifyArtwork()) return;

  // pc_sender.py stores each pixel as a native-endian RGB565 uint16_t. Keep
  // TFT_eSPI's RAM-image byte swapping disabled for this direct transfer, then
  // restore the prior setting in case another drawing path changes it later.
  const bool previousSwapBytes = tft.getSwapBytes();
  tft.setSwapBytes(false);
  tft.pushImage(16, 48, ALBUM_ART_WIDTH, ALBUM_ART_HEIGHT, spotifyAlbumArt);
  tft.setSwapBytes(previousSwapBytes);
  tft.drawRoundRect(16, 48, ALBUM_ART_WIDTH, ALBUM_ART_HEIGHT, 7, COLOR_LINE);
}

void drawNowPlayingHeader() {
  canvas.drawFastHLine(0, 28, SCREEN_WIDTH, COLOR_LINE);

  canvas.setTextDatum(TL_DATUM);
  canvas.setTextColor(COLOR_CYAN, COLOR_BG);
  canvas.setTextFont(2);
  canvas.drawString("NOW PLAYING", 8, 5);

  canvas.fillTriangle(208, 9, 208, 19, 216, 14, COLOR_AMBER);

  canvas.setTextFont(1);
  canvas.setTextColor(COLOR_MUTED, COLOR_BG);
  String source = shortenToWidth(mediaSource, 52, 1);
  canvas.drawString(source, 220, 10);

  drawWiFiIcon(271, 4,
               WiFi.status() == WL_CONNECTED ? COLOR_MUTED : COLOR_ERROR);

  canvas.setTextDatum(TR_DATUM);
  if (WiFi.status() == WL_CONNECTED) {
    canvas.drawString(String(WiFi.RSSI()), 316, 10);
  } else {
    canvas.drawString("OFF", 316, 10);
  }
  canvas.setTextDatum(TL_DATUM);
}

void drawMediaText() {
  canvas.setTextDatum(TL_DATUM);

  canvas.setTextFont(1);
  canvas.setTextColor(COLOR_CYAN, COLOR_MEDIA_LEFT);
  const String state = pcOnline
                           ? (mediaPlaying ? "PLAYING FROM " : "PAUSED / ")
                           : "STATUS / ";
  canvas.drawString(shortenToWidth(state + upperText(mediaSource), 144, 1),
                    158, 50);

  String title = mediaTitle;
  if (title.length() == 0) title = "Nothing playing";

  canvas.setTextColor(COLOR_TEXT, COLOR_MEDIA_LEFT);
  canvas.setTextFont(4);
  String remaining = title;

  for (int line = 0; line < 3 && remaining.length() > 0; line++) {
    String lineText;

    while (remaining.length() > 0) {
      const int split = remaining.indexOf(' ');
      const String word = split < 0 ? remaining
                                    : remaining.substring(0, split);
      const String rest = split < 0 ? ""
                                    : remaining.substring(split + 1);
      const String candidate = lineText.length() == 0
                                   ? word
                                   : lineText + " " + word;

      if (canvas.textWidth(candidate, 4) <= 144) {
        lineText = candidate;
        remaining = rest;
        continue;
      }

      if (lineText.length() == 0) {
        lineText = shortenToWidth(word, 144, 4);
        remaining = rest;
      }
      break;
    }

    if (line == 2 && remaining.length() > 0) {
      lineText = shortenToWidth(lineText + " " + remaining, 144, 4);
      remaining = "";
    }

    canvas.drawString(lineText, 158, 64 + line * 28, 4);
  }

  String detail = mediaArtist;
  if (detail.length() == 0) detail = "Unknown artist";
  canvas.setTextFont(2);
  canvas.setTextColor(COLOR_MUTED, COLOR_MEDIA_LEFT);
  canvas.drawString(shortenToWidth(detail, 144, 2), 158, 160, 2);

  canvas.fillRoundRect(16, 190, 288, 6, 3, rgb565(37, 49, 59));

  long shownPosition = mediaPositionSeconds;
  if (pcOnline && mediaPlaying) {
    shownPosition += (millis() - lastPcUpdateMs) / 1000UL;
  }

  if (mediaDurationSeconds > 0) {
    shownPosition = constrain(shownPosition, 0L, mediaDurationSeconds);
    const int progressWidth = static_cast<int>(
        (288.0F * shownPosition) / mediaDurationSeconds);
    if (progressWidth > 0) {
      canvas.fillRoundRect(16, 190, progressWidth, 6, 3, COLOR_CYAN);
    }
  }

  canvas.setTextColor(COLOR_MUTED, COLOR_MEDIA_LEFT);
  canvas.setTextFont(2);
  canvas.setTextDatum(TL_DATUM);
  canvas.drawString(secondsToClock(shownPosition), 16, 205, 2);
  canvas.setTextDatum(TR_DATUM);
  canvas.drawString(mediaDurationSeconds > 0
                        ? secondsToClock(mediaDurationSeconds)
                        : "--:--",
                    304, 205, 2);
  canvas.setTextDatum(TL_DATUM);
}

void drawNowPlayingScreen() {
  drawNowPlayingHeader();
  canvas.fillRoundRect(6, 35, 308, 199, 7, COLOR_MEDIA_LEFT);
  canvas.drawRoundRect(6, 35, 308, 199, 7, COLOR_LINE);

  drawMediaArtwork();
  drawMediaText();
}

void drawMonitorHeader() {
  canvas.drawFastHLine(0, 28, SCREEN_WIDTH, COLOR_LINE);

  canvas.setTextDatum(TL_DATUM);
  canvas.setTextFont(2);
  canvas.setTextColor(COLOR_CYAN, COLOR_BG);
  canvas.drawString("SYSTEM MONITOR", 8, 5);

  canvas.setTextFont(1);
  if (pcOnline) {
    canvas.fillCircle(196, 14, 3, COLOR_CYAN);
    canvas.setTextColor(COLOR_CYAN, COLOR_BG);
    canvas.drawString("PC LIVE", 204, 10);
  } else if (WiFi.status() == WL_CONNECTED) {
    canvas.setTextColor(COLOR_AMBER, COLOR_BG);
    canvas.drawString("IP " + WiFi.localIP().toString(), 148, 10);
  } else {
    canvas.fillCircle(196, 14, 3, COLOR_ERROR);
    canvas.setTextColor(COLOR_ERROR, COLOR_BG);
    canvas.drawString("PC OFF", 204, 10);
  }

  drawWiFiIcon(271, 4,
               WiFi.status() == WL_CONNECTED ? COLOR_MUTED : COLOR_ERROR);

  canvas.setTextDatum(TR_DATUM);
  canvas.setTextColor(COLOR_MUTED, COLOR_BG);
  if (WiFi.status() == WL_CONNECTED) {
    canvas.drawString(String(WiFi.RSSI()), 316, 10);
  } else {
    canvas.drawString("OFF", 316, 10);
  }
  canvas.setTextDatum(TL_DATUM);
}

void drawMonitorMetricCard(int x, const String &label, const String &value,
                           const String &footer, uint16_t valueColor) {
  canvas.fillRoundRect(x, 35, 98, 56, 6, COLOR_PANEL);
  canvas.drawRoundRect(x, 35, 98, 56, 6, COLOR_LINE);

  canvas.setTextDatum(TL_DATUM);
  canvas.setTextFont(1);
  canvas.setTextColor(COLOR_MUTED, COLOR_PANEL);
  canvas.drawString(shortenToWidth(label, 82, 1), x + 8, 40);

  canvas.setTextFont(4);
  canvas.setTextColor(valueColor, COLOR_PANEL);
  canvas.drawString(shortenToWidth(value, 82, 4), x + 8, 50, 4);

  canvas.setTextFont(1);
  canvas.setTextColor(COLOR_MUTED, COLOR_PANEL);
  canvas.drawString(shortenToWidth(footer, 82, 1), x + 8, 80);
}

void drawStoragePanel() {
  canvas.fillRoundRect(6, 97, 308, 47, 6, COLOR_PANEL);
  canvas.drawRoundRect(6, 97, 308, 47, 6, COLOR_LINE);

  canvas.setTextDatum(TL_DATUM);
  canvas.setTextFont(1);
  canvas.setTextColor(COLOR_MUTED, COLOR_PANEL);
  canvas.drawString("SSD COMPOSITE TEMPS", 12, 101);

  for (int index = 0; index < STORAGE_TEMPERATURE_COUNT; index++) {
    const int x = 12 + index * 102;
    if (index > 0) {
      canvas.drawLine(x - 6, 112, x - 6, 138, COLOR_LINE);
    }

    canvas.setTextDatum(TL_DATUM);
    canvas.setTextFont(1);
    canvas.setTextColor(COLOR_CYAN, COLOR_PANEL);
    canvas.drawString(shortenToWidth(storageName[index], 58, 1), x, 115);

    String value = nullableTemperature(storageTempC[index]);
    if (value != "--") value += " C";
    canvas.setTextDatum(TR_DATUM);
    canvas.setTextFont(2);
    canvas.setTextColor(isnan(storageTempC[index]) ? COLOR_AMBER : COLOR_TEXT,
                        COLOR_PANEL);
    canvas.drawString(value, x + 91, 124, 2);
  }
  canvas.setTextDatum(TL_DATUM);
}

void drawFanReading(int x, const String &label, int rpm) {
  canvas.setTextDatum(TL_DATUM);
  canvas.setTextFont(2);
  canvas.setTextColor(COLOR_CYAN, COLOR_PANEL);
  canvas.drawString(label, x + 8, 171, 2);

  canvas.setTextFont(4);
  canvas.setTextColor(rpm < 0 ? COLOR_MUTED : COLOR_TEXT, COLOR_PANEL);
  canvas.drawString(shortenToWidth(nullableRpm(rpm), 82, 4), x + 8, 189, 4);

  canvas.setTextFont(1);
  canvas.setTextColor(COLOR_MUTED, COLOR_PANEL);
  canvas.drawString("RPM", x + 8, 220);
}

void drawFanPanel() {
  canvas.fillRoundRect(6, 150, 308, 84, 6, COLOR_PANEL);
  canvas.drawRoundRect(6, 150, 308, 84, 6, COLOR_LINE);

  canvas.setTextDatum(TL_DATUM);
  canvas.setTextFont(1);
  canvas.setTextColor(COLOR_MUTED, COLOR_PANEL);
  canvas.drawString("FAN SPEEDS (RPM)", 12, 154);

  canvas.drawLine(108, 168, 108, 228, COLOR_LINE);
  canvas.drawLine(210, 168, 210, 228, COLOR_LINE);

  drawFanReading(12, "F2", systemFanRpm[1]);
  drawFanReading(114, "F6", systemFanRpm[5]);
  drawFanReading(216, "F7", systemFanRpm[6]);
  canvas.setTextDatum(TL_DATUM);
}

void drawMonitoringScreen() {
  drawMonitorHeader();

  String cpuValue = nullableTemperature(cpuTempC);
  if (cpuValue != "--") cpuValue += " C";
  String gpuValue = nullableTemperature(gpuTempC);
  if (gpuValue != "--") gpuValue += " C";
  String roomValue = "--.-F";
  if (!isnan(roomTempF)) roomValue = String(roomTempF, 1) + "F";
  String roomFooter = "RH --%";
  if (!isnan(roomHumidity)) {
    roomFooter = "RH " + String(roomHumidity, 0) + "%";
  }

  const uint16_t cpuColor = !pcOnline ? COLOR_ERROR
                                      : (isnan(cpuTempC) ? COLOR_AMBER
                                                        : COLOR_TEXT);
  const uint16_t gpuColor = !pcOnline ? COLOR_ERROR
                                      : (isnan(gpuTempC) ? COLOR_AMBER
                                                        : COLOR_TEXT);
  drawMonitorMetricCard(6, "CPU PACKAGE", cpuValue, "EXACT SENSOR", cpuColor);
  drawMonitorMetricCard(111, "GPU TEMP", gpuValue, "NVIDIA", gpuColor);
  drawMonitorMetricCard(216, "ROOM TEMP", roomValue, roomFooter, COLOR_TEXT);

  drawStoragePanel();
  drawFanPanel();
}

void drawDisplay() {
  const bool showSpotifyArtwork = shouldDrawSpotifyArtwork();

  canvas.fillSprite(COLOR_BG);

  if (pcOnline && mediaPlaying) {
    drawNowPlayingScreen();
  } else {
    drawMonitoringScreen();
  }

  if (showSpotifyArtwork && spotifyArtworkVisibleOnTft) {
    // Preserve the full-color artwork already on the LCD. Without this
    // transparent window, the once-per-second UI refresh briefly paints the
    // panel background over the cover before restoring it, which looks like
    // flashing.
    canvas.fillRect(16, 48, ALBUM_ART_WIDTH, ALBUM_ART_HEIGHT,
                    COLOR_ARTWORK_TRANSPARENT);
    canvas.pushSprite(0, 0, COLOR_ARTWORK_TRANSPARENT);
  } else {
    // A page transition needs one complete opaque frame so no pixels from the
    // previous page remain behind the new layout.
    canvas.pushSprite(0, 0);
  }

  if (showSpotifyArtwork &&
      (!spotifyArtworkVisibleOnTft || spotifyArtworkNeedsRefresh)) {
    drawFullColorSpotifyArtwork();
    spotifyArtworkNeedsRefresh = false;
  }

  spotifyArtworkVisibleOnTft = showSpotifyArtwork;
  displayDirty = false;
  lastDrawMs = millis();
}

// --------------------------- HTTP service --------------------------

String jsonNumberOrNull(float value, unsigned int decimals = 1) {
  return isnan(value) ? "null" : String(value, decimals);
}

void handleStatusRequest() {
  String json = "{";
  json += "\"device\":\"CYD Room Display\",";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"pc_online\":" + String(pcOnline ? "true" : "false") + ",";
  json += "\"cpu_c\":" + jsonNumberOrNull(cpuTempC) + ",";
  json += "\"gpu_c\":" + jsonNumberOrNull(gpuTempC) + ",";
  json += "\"room_f\":" + jsonNumberOrNull(roomTempF) + ",";
  json += "\"humidity\":" + jsonNumberOrNull(roomHumidity, 0) + ",";
  json += "\"screen\":\"";
  json += (pcOnline && mediaPlaying) ? "now_playing" : "monitor";
  json += "\"";
  json += "}";
  webServer.send(200, "application/json", json);
}

void handlePcUpdate() {
  if (!webServer.hasArg("key") || webServer.arg("key") != DISPLAY_API_KEY) {
    webServer.send(403, "application/json", "{\"ok\":false,\"error\":\"bad key\"}");
    return;
  }

  cpuTempC = parseTemperatureArgument("cpu");
  gpuTempC = parseTemperatureArgument("gpu");

  for (int index = 0; index < SYSTEM_FAN_COUNT; index++) {
    systemFanRpm[index] = parseRpmArgument("fan" + String(index + 1));
  }
  for (int index = 0; index < GPU_FAN_COUNT; index++) {
    gpuFanRpm[index] = parseRpmArgument("gfan" + String(index + 1));
  }
  for (int index = 0; index < STORAGE_TEMPERATURE_COUNT; index++) {
    const String number = String(index + 1);
    storageTempC[index] = parseTemperatureArgument("ssd" + number);

    const String nameArgument = "ssd" + number + "name";
    if (webServer.hasArg(nameArgument)) {
      const String receivedName = limitedText(webServer.arg(nameArgument), 18);
      storageName[index] = receivedName.length() > 0
                               ? receivedName
                               : "SSD " + number;
    }
  }

  if (webServer.hasArg("title")) {
    mediaTitle = limitedText(webServer.arg("title"), 120);
  }
  if (webServer.hasArg("artist")) {
    mediaArtist = limitedText(webServer.arg("artist"), 100);
  }
  if (webServer.hasArg("source")) {
    mediaSource = limitedText(webServer.arg("source"), 32);
  }
  bool requestedArtworkMissing = false;
  if (webServer.hasArg("art")) {
    const String artworkState = webServer.arg("art");
    if (artworkState == "clear") {
      spotifyAlbumArtAvailable = false;
      spotifyArtworkNeedsRefresh = false;
    } else if (artworkState == "ready" && !spotifyAlbumArtAvailable) {
      requestedArtworkMissing = true;
    }
  }

  mediaPlaying = webServer.hasArg("playing") &&
                 webServer.arg("playing") == "1";

  mediaPositionSeconds = webServer.hasArg("position")
                             ? max(0L, webServer.arg("position").toInt())
                             : 0;
  mediaDurationSeconds = webServer.hasArg("duration")
                             ? max(0L, webServer.arg("duration").toInt())
                             : 0;

  if (mediaDurationSeconds > 0) {
    mediaPositionSeconds = min(mediaPositionSeconds, mediaDurationSeconds);
  }

  pcHasConnected = true;
  pcOnline = true;
  lastPcUpdateMs = millis();
  displayDirty = true;

  if (requestedArtworkMissing) {
    webServer.send(409, "application/json",
                   "{\"ok\":false,\"error\":\"album art missing\"}");
  } else {
    webServer.send(200, "application/json", "{\"ok\":true}");
  }
}

void handleAlbumArtUpload() {
  HTTPUpload &upload = webServer.upload();

  if (upload.status == UPLOAD_FILE_START) {
    albumArtUploadBytes = 0;
    albumArtUploadAuthorized =
        webServer.hasArg("key") && webServer.arg("key") == DISPLAY_API_KEY;
    albumArtUploadValid = albumArtUploadAuthorized;
    return;
  }

  if (upload.status == UPLOAD_FILE_WRITE) {
    if (!albumArtUploadValid ||
        albumArtUploadBytes + upload.currentSize > ALBUM_ART_BYTE_COUNT) {
      albumArtUploadValid = false;
      return;
    }

    memcpy(reinterpret_cast<uint8_t *>(spotifyAlbumArt) + albumArtUploadBytes,
           upload.buf, upload.currentSize);
    albumArtUploadBytes += upload.currentSize;
    return;
  }

  if (upload.status == UPLOAD_FILE_END) {
    albumArtUploadValid =
        albumArtUploadValid && albumArtUploadBytes == ALBUM_ART_BYTE_COUNT;
    if (albumArtUploadValid) {
      spotifyAlbumArtAvailable = true;
      spotifyArtworkNeedsRefresh = true;
      displayDirty = true;
    }
    return;
  }

  if (upload.status == UPLOAD_FILE_ABORTED) {
    albumArtUploadValid = false;
  }
}

void finishAlbumArtUpload() {
  const bool authorized = albumArtUploadAuthorized;
  const bool valid = albumArtUploadValid;
  albumArtUploadAuthorized = false;
  albumArtUploadValid = false;

  if (!authorized) {
    webServer.send(403, "application/json",
                   "{\"ok\":false,\"error\":\"bad key\"}");
    return;
  }

  if (!valid) {
    webServer.send(400, "application/json",
                   "{\"ok\":false,\"error\":\"album art must be 128x128 RGB565\"}");
    return;
  }

  webServer.send(200, "application/json", "{\"ok\":true}");
}

void configureWebServer() {
  webServer.on("/", HTTP_GET, handleStatusRequest);
  webServer.on("/status", HTTP_GET, handleStatusRequest);
  webServer.on("/update", HTTP_POST, handlePcUpdate);
  webServer.on("/update", HTTP_GET, handlePcUpdate);
  webServer.on("/art", HTTP_POST, finishAlbumArtUpload,
               handleAlbumArtUpload);
  webServer.onNotFound([]() {
    webServer.send(404, "application/json", "{\"ok\":false,\"error\":\"not found\"}");
  });
  webServer.begin();
}

// -------------------------- UDP discovery --------------------------

void handleDiscovery() {
  const int packetSize = discoveryUdp.parsePacket();
  if (packetSize <= 0) return;

  char packet[64];
  const int bytesRead = discoveryUdp.read(packet, sizeof(packet) - 1);
  if (bytesRead <= 0) return;
  packet[bytesRead] = '\0';

  if (String(packet) != DISCOVERY_REQUEST) return;

  discoveryUdp.beginPacket(discoveryUdp.remoteIP(), discoveryUdp.remotePort());
  discoveryUdp.print(DISCOVERY_REPLY);
  discoveryUdp.endPacket();
}

// -------------------------- Sensor handling ------------------------

void readRoomSensor() {
  const float humidity = dht.readHumidity();
  const float celsius = dht.readTemperature();

  if (isnan(humidity) || isnan(celsius)) return;

  const float fahrenheit = celsius * 9.0F / 5.0F + 32.0F;
  if (fahrenheit < -50.0F || fahrenheit > 150.0F ||
      humidity < 0.0F || humidity > 100.0F) {
    return;
  }

  if (isnan(roomTempF) || fabsf(roomTempF - fahrenheit) >= 0.05F ||
      isnan(roomHumidity) || fabsf(roomHumidity - humidity) >= 0.5F) {
    displayDirty = true;
  }

  roomTempC = celsius;
  roomTempF = fahrenheit;
  roomHumidity = humidity;
}

// -------------------------- Wi-Fi handling -------------------------

void beginWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setHostname("room-display");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWiFiAttemptMs = millis();
}

void maintainWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    if (!networkServicesStarted) {
      configureWebServer();
      discoveryUdp.begin(DISCOVERY_PORT);
      MDNS.begin("room-display");
      MDNS.addService("http", "tcp", 80);
      networkServicesStarted = true;
      displayDirty = true;

      Serial.print("Room Display address: http://");
      Serial.println(WiFi.localIP());
    }
    return;
  }

  if (millis() - lastWiFiAttemptMs >= WIFI_RETRY_INTERVAL_MS) {
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    lastWiFiAttemptMs = millis();
    displayDirty = true;
  }
}

// -------------------------- Arduino setup --------------------------

void setup() {
  Serial.begin(115200);

  pinMode(BACKLIGHT_PIN, OUTPUT);
  digitalWrite(BACKLIGHT_PIN, HIGH);

  tft.init();
  tft.setRotation(1);
  tft.fillScreen(COLOR_BG);

  canvas.setColorDepth(8);
  if (canvas.createSprite(SCREEN_WIDTH, SCREEN_HEIGHT) == nullptr) {
    Serial.println("ERROR: Unable to allocate the display canvas.");
    while (true) delay(1000);
  }

  dht.begin();
  beginWiFi();
  drawDisplay();

  lastSensorReadMs = millis();
}

// --------------------------- Arduino loop --------------------------

void loop() {
  maintainWiFi();

  if (WiFi.status() == WL_CONNECTED && networkServicesStarted) {
    webServer.handleClient();
    handleDiscovery();
  }

  const unsigned long now = millis();

  if (now - lastSensorReadMs >= SENSOR_INTERVAL_MS) {
    lastSensorReadMs = now;
    readRoomSensor();
  }

  if (pcOnline && now - lastPcUpdateMs >= PC_OFFLINE_TIMEOUT_MS) {
    pcOnline = false;
    cpuTempC = NAN;
    gpuTempC = NAN;
    for (int index = 0; index < SYSTEM_FAN_COUNT; index++) {
      systemFanRpm[index] = -1;
    }
    for (int index = 0; index < GPU_FAN_COUNT; index++) {
      gpuFanRpm[index] = -1;
    }
    for (int index = 0; index < STORAGE_TEMPERATURE_COUNT; index++) {
      storageTempC[index] = NAN;
    }
    mediaPlaying = false;
    mediaPositionSeconds = 0;
    mediaDurationSeconds = 0;
    mediaSource = "PC OFFLINE";
    mediaTitle = pcHasConnected ? "PC connection lost" : "Waiting for PC";
    mediaArtist = "Run the Windows companion";
    displayDirty = true;
  }

  const bool progressNeedsUpdate = pcOnline && mediaPlaying;
  if ((displayDirty || progressNeedsUpdate) &&
      now - lastDrawMs >= DRAW_INTERVAL_MS) {
    drawDisplay();
  }

  delay(2);
}
