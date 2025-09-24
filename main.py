from machine import Pin, PWM, Timer, ADC
import utime
from rotary_irq_rp2 import RotaryIRQ
import network, ntptime, urequests

from util import *
from ota import OTAUpdater
from WIFI_CONFIG import *

#################################################################
##################### CONSTANT VARIABLES ########################
#################################################################

SAFETY_TEMP = 75 # degrees C
SPEED_SCALE = 1.5

# Palo Alto
LAT = 37.4419
LNG = -122.1430

# OTA Updates
FIRMWARE_URL = "https://raw.githubusercontent.com/theLiamFC/PicoLamp/"

# Turn on and turn off start times (30 min transition)
LIGHT_SCHEDULE = [
    [(7,30),(22,30)], # Monday:     [AM ON, PM OFF]
    [(7,30),(22,30)], # Tuesday:    [AM ON, PM OFF]
    [(7,30),(22,30)], # Wednesday:  [AM ON, PM OFF]
    [(7,30),(22,30)], # Thursday:   [AM ON, PM OFF]
    [(8,30),(23,30)], # Friday:     [AM ON, PM OFF]
    [(8,30),(23,30)], # Saturday:   [AM ON, PM OFF]
    [(8,30),(22,30)]  # Sunday:     [AM ON, PM OFF]
]

#################################################################
######################## BUTTON HANDLER #########################
#################################################################

def button_handler(pin):
    global buttonState, lastButtonTime, buttonStartTime
    
    current_time = utime.ticks_ms()
    if utime.ticks_diff(current_time, lastButtonTime) > debounceThresh:
        if pin.value() == 1:  # Button pressed
            print(f"Button pressed at {current_time}")
            buttonState = -1  # Pressed but not yet determined
            buttonStartTime = current_time
        else:  # Button released
            # Check if it was a long press
            if utime.ticks_diff(current_time, buttonStartTime) > longPressThresh:
                print(f"Button long released at {current_time}")
                buttonState = 2
            else:
                print(f"Button released at {current_time}")
                buttonState = 1
            lastButtonTime = current_time

#################################################################
##################### HARDWARE SETUP  ###########################
#################################################################

# Onboard LED
onboardLED = Pin("LED", Pin.OUT)
onboardLED.value(1)

# Setup LED with PWM
ledPinNum = 11
ledPWM = PWM(Pin(ledPinNum))
ledPWM.freq(200)

# Define the ADC pin connected to the temperature TempSensor (ADC4)
TempSensor = ADC(4)

# Setup Rotary encoder
Rotary = RotaryIRQ(
    pin_num_clk=2,
    pin_num_dt=3,
    min_val=-1,
    max_val=100,
    reverse=True,
    range_mode=RotaryIRQ.RANGE_BOUNDED,
    pull_up=True,
    invert=False
)

# Setup button
Button = Pin(4, Pin.IN, Pin.PULL_DOWN)

# Set up button interrupt
Button.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=button_handler)

#################################################################
##################### MODULE VARIABLES ##########################
#################################################################

# Internet variables
connected_to_wifi = False

# Button variables
buttonState = 0  # 0: not pressed, 1: pressed, 2: long pressed
lastButtonTime = 0 # for debouncing
debounceThresh = 200 # ms
buttonStartTime = 0 # for long press
longPressThresh = 600  # ms

ledMode = True

# Brightness variables
currBrightness = 0
targetBrightness = 0
stepSize = 0.1

newRotary = False
lastIndicatorTime = utime.time()

# Speed-sensitive encoder variables
lastRotVal = Rotary.value()
lastRotTime = utime.ticks_ms()
rotSpeed = 0  # Speed in clicks per second

# Sun Variables
lamp_setting = False
lamp_rising = False
start_transition = (0,0,0)
end_transition = (0,0,0)

#################################################################
###################### MAIN CODE ################################
#################################################################

try:
    while True:
        temp_c = read_temp(TempSensor)
    
        if temp_c > SAFETY_TEMP:
            ledMode = False
        else:
            if not connected_to_wifi:
                connected_to_wifi = connect_wifi(SSID, PASSWORD)
                ntptime.settime()
                sunrise, sunset = get_sunrise_sunset_pacific(LAT, LNG)
            else:
                try:
                    year, month, day, hour, minute, second, weekday, day_of_year = get_local_time()

                    # Update once an hour
                    if hour == 3 and minute == 0 and second == 0:
                        print("Fetching time, sun schedule, git repo...")
                        ntptime.settime()
                        sunrise, sunset = get_sunrise_sunset_pacific(LAT, LNG)

                        main_ota_updater = OTAUpdater(SSID, PASSWORD, FIRMWARE_URL, "main.py")
                        ota_updater = OTAUpdater(SSID, PASSWORD, FIRMWARE_URL, "util.py")
                        ota_updater.download_and_install_update_if_available()


                    if (hour, minute) == LIGHT_SCHEDULE[weekday][0] and not lamp_rising: # fade on in the morning
                        lamp_rising = True
                        start_transition = (hour, minute, second)
                        end_transition = increment_time(0,30)
                        print("Beginning morning fade on")
                    elif (hour, minute) == (9,30) and not lamp_setting: # fade off at 9:30
                        lamp_setting = True
                        start_transition = (hour, minute, second)
                        end_transition = increment_time(0, 30)
                        print("Beginning morning fade off")
                    elif increment_time(0, 30) == sunset and not lamp_rising: # fade on at sunset
                        print("beginning sunset fade on")
                        lamp_rising = True
                        start_transition = (hour, minute, second)
                        end_transition = increment_time(0, 30)
                        print("Beginning sunset fade on")
                    elif (hour, minute) == LIGHT_SCHEDULE[weekday][1] and not lamp_setting: # fade off at night
                        lamp_setting = True
                        start_transition = (hour, minute, second)
                        end_transition = increment_time(0, 30)
                        print("Beginning night fade off")
                except Exception as e:
                    print(f"Internet update failed: {e}")
                    connected_to_wifi = False        
                
            # Blink onboard led
            if utime.time() - lastIndicatorTime > 1:
                onboardLED.value(not onboardLED.value())
                lastIndicatorTime = utime.time()

            # Check if encoder value changed
            val = Rotary.value()
            if val != lastRotVal:
                lamp_setting = False
                lamp_rising = False

                # Calculate encoder speed
                current_time = utime.ticks_ms()
                time_diff = utime.ticks_diff(current_time, lastRotTime)
                
                # Avoid division by zero and convert to clicks per second
                if time_diff > 0:
                    rotSpeed = 1000 / time_diff
                else:
                    rotSpeed = 30  # Default high speed if time difference is too small
                
                # Calculate adaptive step size
                adaptive_step = max(1,int((rotSpeed / 10)**SPEED_SCALE))
                
                # Apply the step in the correct direction
                if val > lastRotVal:
                    targetBrightness = min(100, lastRotVal + adaptive_step)
                else:
                    targetBrightness = max(0, lastRotVal - adaptive_step)
                
                # Update Rotary value to match our calculated target
                Rotary.set(value=targetBrightness)
                val = targetBrightness
                
                newRotary = True
                print(f"Rotary value: {val}, Speed: {rotSpeed:.1f} clicks/s, Step: {adaptive_step}")
                
                # Update tracking variables
                lastRotVal = val
                lastRotTime = current_time

            # Check if button was pressed
            if buttonState > 0 or (not ledMode and newRotary):
                lamp_setting = False
                lamp_rising = False

                if buttonState != 2:
                    print(f"LED toggled: {ledMode}")
                    newRotary = False
                    if ledMode > 0: ledMode = 0
                    else: ledMode = 1
                elif buttonState == 2:
                    print("Auto brightness enabled")
                    ledMode = 2
                
                buttonState = 0

            if ledMode == 0: # off
                targetBrightness = 0
            elif ledMode == 1: # on
                targetBrightness = val
            elif ledMode == 2: # auto
                targetBrightness = val

            # Smoothly interpolate brightness
            if lamp_rising:
                currBrightness = get_curr_fade(start_transition, end_transition, fade_on=True)
                if not 0 <= currBrightness <= 100:
                    lamp_rising = False
            elif lamp_setting:
                currBrightness = get_curr_fade(start_transition, end_transition, fade_on=False)
                if not 0 <= currBrightness <= 100:
                    lamp_setting = False
            else:
                delta = abs(targetBrightness - currBrightness)
                if currBrightness < targetBrightness:
                    currBrightness = min(currBrightness + delta * stepSize / 10, targetBrightness)
                elif currBrightness > targetBrightness:
                    currBrightness = max(currBrightness - delta * stepSize / 10, targetBrightness)


            duty = map_value(currBrightness, 0, 100, 0, 65535)
            ledPWM.duty_u16(duty)

            # Small delay to prevent hogging the CPU
            utime.sleep_ms(5)

except KeyboardInterrupt:
    # Clean up
    ledPWM.duty_u16(0)
    ledPWM.deinit()
    print("Program terminated")


