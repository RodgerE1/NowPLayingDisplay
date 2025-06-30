Now Playing Display
This project allows you to display "Now Playing" information from your Windows PC on an ESP32-driven LCD, and also outputs the data to a JSON file. It's designed for users who want a physical display for their currently playing media.

It consists of two main parts:

now_playing_sender.py (Python Script for Windows PC): This script runs on your Windows 11 PC, polls the system for media playback information, and sends it via serial to an ESP32. It also writes the current media information to a JSON file.

ESP32 Arduino Sketch (for ESP32 Microcontroller): This sketch runs on an ESP32 board connected to an I2C LCD (e.g., 16x2). It receives serial data from the PC script and displays the title, artist, and playback status. The backlight of the LCD is controlled by the playback status.

Features
Real-time Updates: Displays current song title, artist, and playback status.

Serial Communication: Sends data from PC to ESP32 via a USB-to-serial connection.

JSON Output: Saves media information to a local JSON file for other applications to use.

System Tray Integration: Runs in the Windows system tray with a tooltip showing status.

Single Instance: Ensures only one instance of the Python script runs at a time.

Dynamic Backlight: ESP32 sketch controls LCD backlight based on playback status (ON when playing, OFF when paused/stopped).

Line Wrapping: ESP32 sketch intelligently wraps long titles/artists across two lines on a 16x2 LCD.

Requirements
PC (Windows 11)
Python 3.x

pyserial

pystray

Pillow (PIL)

winsdk

ESP32
ESP32 development board (e.g., ESP32 DevKitC)

16x2 I2C LCD display

Arduino IDE

LiquidCrystal_I2C library for Arduino

Wire library (usually built-in)

USB cable for serial connection between PC and ESP32

Setup Instructions
1. PC Setup (now_playing_sender.py)
Clone the Repository:
If you haven't already, clone this repository to your local machine:

git clone https://github.com/RodgerE1/NowPLayingDisplay.git
cd NowPLayingDisplay

Install Python Dependencies:
It's recommended to use a Python virtual environment.

python -m venv venv
.\venv\Scripts\activate   # On Windows
# source venv/bin/activate # On macOS/Linux
pip install pyserial pystray Pillow winsdk

Configure the Python Script:
Open now_playing_sender.py in a text editor.

Adjust ESP32_PORT: Change 'COM4' to the COM port assigned to your ESP32 when connected to your PC. You can find this in Windows Device Manager under "Ports (COM & LPT)".

Adjust JSON_FILENAME: If you want the JSON output file in a different location, change r"e:\now_playing_sender.json".

Run the Python Script:

python now_playing_sender.py

The script will run in the background and appear as an icon in your system tray.

2. ESP32 Setup (Arduino Sketch)
Install Arduino IDE:
Download and install the Arduino IDE.

Add ESP32 Board Support:

Go to File > Preferences in Arduino IDE.

In "Additional Boards Manager URLs", add:
https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json

Go to Tools > Board > Boards Manager....

Search for "esp32" and install the "ESP32 by Espressif Systems" package.

Select your specific ESP32 board from Tools > Board.

Install LiquidCrystal_I2C Library:

Go to Sketch > Include Library > Manage Libraries....

Search for "LiquidCrystal I2C" and install the library by Frank de Brabander.

Wire the LCD to ESP32:
Connect your I2C LCD to the ESP32. Standard I2C pins for ESP32 are:

SDA: GPIO 21

SCL: GPIO 22

VCC: 3.3V or 5V (depending on your LCD module's requirements)

GND: GND

The sketch uses GPIO 18 for the backlight control. You'll need to connect a digital pin (e.g., GPIO 18) from your ESP32 to the backlight control pin of your I2C LCD module (if it has one, often labeled LED+ or similar, usually through a transistor or directly if it's a simple on/off). If your I2C module handles backlight internally, you might not need the BACKLIGHT_PIN and digitalWrite calls, but the code is set up to use it.

Upload the Arduino Sketch:

Copy the provided Arduino sketch code into a new sketch in the Arduino IDE.

Verify the LCD_ADDR (0x27 or 0x3F are common) matches your LCD's I2C address. You might need an I2C scanner sketch to find the correct address if 0x27 doesn't work.

Select the correct COM port for your ESP32 under Tools > Port.

Click the "Upload" button.

Usage
Ensure both the Python script is running on your PC and the Arduino sketch is uploaded to your ESP32.

Play media on your Windows PC (e.g., Spotify, YouTube, Windows Media Player).

The "Now Playing" information should appear on your LCD, and the backlight will turn on when media is actively playing (status 4).

The now_playing_sender.json file will be updated with the full media information.

Troubleshooting
Python Script not starting: Check your Python installation and ensure all dependencies are installed.

"Another instance is already running": The script is designed to run as a single instance. If you see this, check your task manager for pythonw.exe or python.exe processes and terminate any existing now_playing_sender.py instances.

Serial Port Errors:

Verify the ESP32_PORT in now_playing_sender.py is correct.

Ensure the ESP32 is connected and its drivers are installed.

Make sure no other application is using the COM port.

LCD not displaying:

Check your wiring connections (SDA, SCL, VCC, GND).

Verify the LCD_ADDR in the Arduino sketch is correct for your LCD.

Ensure the LiquidCrystal_I2C library is installed.

Check the serial monitor in Arduino IDE for any error messages from the ESP32.

Backlight not working:

Ensure BACKLIGHT_PIN (GPIO 18) is correctly connected to your LCD's backlight control.

Verify your LCD module supports backlight control via a digital pin.

Contributing
Feel free to open issues or submit pull requests if you have suggestions or improvements!