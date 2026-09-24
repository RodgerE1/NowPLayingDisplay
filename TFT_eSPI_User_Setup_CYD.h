/*
  Backup TFT_eSPI User_Setup.h for the ESP32-2432S028R CYD.

  This is the relevant configuration from the Random Nerd Tutorials source:
  https://raw.githubusercontent.com/RuiSantosdotme/ESP32-TFT-Touchscreen/main/configs/User_Setup.h

  If a TFT_eSPI update causes a white screen, rename this file User_Setup.h
  and use it to replace:
    <Arduino sketchbook>\libraries\TFT_eSPI\User_Setup.h
*/

#define USER_SETUP_INFO "ESP32-2432S028R Random Nerd Tutorials"

#define ILI9341_2_DRIVER

#define TFT_WIDTH 240
#define TFT_HEIGHT 320

#define TFT_BL 21
#define TFT_BACKLIGHT_ON HIGH

#define TFT_MISO 12
#define TFT_MOSI 13
#define TFT_SCLK 14
#define TFT_CS 15
#define TFT_DC 2
#define TFT_RST -1
#define TOUCH_CS 33

#define LOAD_GLCD
#define LOAD_FONT2
#define LOAD_FONT4
#define LOAD_FONT6
#define LOAD_FONT7
#define LOAD_FONT8
#define LOAD_GFXFF
#define SMOOTH_FONT

#define SPI_FREQUENCY 55000000
#define SPI_READ_FREQUENCY 20000000
#define SPI_TOUCH_FREQUENCY 2500000

#define USE_HSPI_PORT
