#!/usr/bin/env python3

from PIL import ImageGrab
import pyautogui

# Take screenshot
# Get main screen size from pyautogui
screen_width, screen_height = pyautogui.size()

# Take screenshot of main screen only
img = ImageGrab.grab(bbox=(0, 0, screen_width, screen_height))

width, height = img.size

# Find red pixel
min_height = height // 2
for y in range(height):
    if y < min_height:
        continue
    for x in range(width):
        r, g, b, a = img.getpixel((x, y))
        if r > 200 and g < 80 and b < 80:  # Adjust threshold for 'red'
            pyautogui.click(x, y)
            print(f"Clicked at ({x}, {y})")
            exit()

print("No red pixel found")
