import os
import utime
import network
import urequests
import ntptime
from machine import Pin, PWM, RTC
from rotary_irq_rp2 import RotaryIRQ

#################################################################
# CONFIGURATION & CONSTANTS
#################################################################

# Location: Stanford, CA
LAT = 37.4275
LNG = -122.1697
UTC_OFFSET = -7  # PDT

# WiFi Credentials
SSID = 'Stanford'
PASSWORD = ''

# Timing & Fading Logic
LOOP_MS = 10
FADE_MINUTES = 30
SUNSET_ADVANCE_MINS = 60  # Start fade 60m before sunset
SUNSET_TARGET = 80.0      # Maximum brightness for automation
MANUAL_STEP = 0.05        # Asymptotic speed for knob feel

# Linear step: 100% / (mins * 60s * 100 samples/s)
LINEAR_STEP = 100 / (FADE_MINUTES * 60 * (1000 / LOOP_MS))

# Logging Config
LOG_FILE = "log.txt"
MAX_LOG_SIZE = 2048  # 2KB recycling threshold

#################################################################
# LOGGING SYSTEM
#################################################################

def log_message(message):
    """Prints to console and manages a recycled log file on flash."""
    lt = utime.localtime()
    t_str = "{:02d}:{:02d}:{:02d}".format(lt[3], lt[4], lt[5])
    full_msg = f"[{t_str}] {message}"
    print(full_msg)
    
    try:
        # Check size and recycle if necessary
        try:
            if os.stat(LOG_FILE)[6] > MAX_LOG_SIZE:
                if "log_old.txt" in os.listdir():
                    os.remove("log_old.txt")
                os.rename(LOG_FILE, "log_old.txt")
        except OSError:
            pass 

        with open(LOG_FILE, "a") as f:
            f.write(full_msg + "\n")
    except Exception as e:
        print(f"Log Error: {e}")

#################################################################
# HARDWARE SETUP
#################################################################

ledPWM = PWM(Pin(11))
ledPWM.freq(500)

Rotary = RotaryIRQ(
    pin_num_clk=2, pin_num_dt=3, 
    min_val=0, max_val=100, 
    reverse=True, range_mode=RotaryIRQ.RANGE_BOUNDED, 
    pull_up=True
)

Button = Pin(4, Pin.IN, Pin.PULL_DOWN)

#################################################################
# STATE VARIABLES
#################################################################

currBrightness = 0.0
targetBrightness = 0.0
last_manual_brightness = 100.0
led_on = False

sunset_time = (19, 0)
automated_fade_active = False
last_sync_day = -1

lastButtonTime = 0
buttonStartTime = 0
LONG_PRESS_MS = 1000
test_mode_triggered = False
last_heartbeat = 0

#################################################################
# NETWORK & API
#################################################################

def is_dst(t):
    # t is a tuple: (year, month, day, hour, minute, second, weekday, yearday)
    year, month, day = t[0], t[1], t[2]
    
    # DST is always between March (3) and November (11)
    if month < 3 or month > 11:
        return False
    if month > 3 and month < 11:
        return True
    
    # Find the second Sunday in March
    # formula: 14 - (1st day of month weekday + 1) % 7
    first_march = utime.mktime((year, 3, 1, 0, 0, 0, 0, 0))
    first_march_wd = utime.localtime(first_march)[6]
    second_sun_march = 14 - (first_march_wd + 1) % 7
    
    # Find the first Sunday in November
    first_nov = utime.mktime((year, 11, 1, 0, 0, 0, 0, 0))
    first_nov_wd = utime.localtime(first_nov)[6]
    first_sun_nov = 7 - (first_nov_wd + 1) % 7
    
    if month == 3:
        return day >= second_sun_march
    if month == 11:
        return day < first_sun_nov

def set_time():
    try:
        # 1. Sync with Atomic Clock (UTC)
        ntptime.settime()
        utc_now = utime.time()
        
        # 2. Get UTC components to check for DST
        t_utc = utime.localtime(utc_now)
        
        # 3. Determine Offset (-7 for PDT, -8 for PST)
        offset = -7 if is_dst(t_utc) else -8
        label = "PDT" if offset == -7 else "PST"
            
        # 4. Calculate Local Time Tuple
        lt = utime.localtime(utc_now + (offset * 3600))
        
        # 5. Manually pack for RTC().datetime() 
        # Expected: (year, month, day, weekday, hours, minutes, seconds, subseconds)
        rtc_tuple = (lt[0], lt[1], lt[2], lt[6], lt[3], lt[4], lt[5], 0)
        
        RTC().datetime(rtc_tuple)
        
        log_message(f"Time synced: {label} (UTC{offset})")
        return True
    except Exception as e:
        log_message(f"Time Sync Failed: {e}")
        return False
    
def connect_wifi():
    log_message("Connecting to WiFi...")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    for _ in range(10):
        if wlan.isconnected():
            log_message("WiFi Connected.")
            return set_time()
        utime.sleep(1)
    log_message("WiFi Failed.")
    return False

def get_sunset_time():
    log_message("Fetching Sunset API...")
    try:
        url = f"http://api.sunrise-sunset.org/json?lat={LAT}&lng={LNG}&formatted=0"
        res = urequests.get(url).json()
        
        # Verify the API actually returned a result
        if 'results' not in res:
            log_message("API Error: No results in response")
            return (19, 15)

        t_str = res['results']['sunset'].split('T')[1]
        utc_h = int(t_str[:2])
        utc_m = int(t_str[3:5])
        
        # Standardize the calculation
        local_h = (utc_h + UTC_OFFSET) % 24
        log_message(f"Sunset synced: {local_h:02d}:{utc_m:02d}")
        return (local_h, utc_m)
    except Exception as e:
        log_message(f"Sunset Fetch Failed: {e}")
        return (19, 15)

def get_trigger_time(base_time, offset_mins):
    h, m = base_time
    total_mins = (h * 60 + m) - offset_mins
    return ((total_mins // 60) % 24, total_mins % 60)

def button_handler(pin):
    global targetBrightness, led_on, last_manual_brightness
    global lastButtonTime, buttonStartTime, test_mode_triggered
    
    current_time = utime.ticks_ms()
    if pin.value() == 1:
        buttonStartTime = current_time
    else:
        duration = utime.ticks_diff(current_time, buttonStartTime)
        if duration > LONG_PRESS_MS:
            test_mode_triggered = True
        elif utime.ticks_diff(current_time, lastButtonTime) > 200:
            led_on = not led_on
            if led_on:
                targetBrightness = last_manual_brightness
                Rotary.set(value=int(last_manual_brightness))
            else:
                targetBrightness = 0
                Rotary.set(value=0)
            lastButtonTime = current_time

Button.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=button_handler)

#################################################################
# MAIN LOOP
#################################################################

if connect_wifi():
    sunset_time = get_sunset_time()

log_message("System Ready. Long-press to test.")

try:
    while True:
        lt = utime.localtime()
        day, hr, mn, sec = lt[2], lt[3], lt[4], lt[5]
        start_time = get_trigger_time(sunset_time, SUNSET_ADVANCE_MINS)

        # 1. Daily Sync
        if day != last_sync_day and hr == 3:
            if connect_wifi():
                sunset_time = get_sunset_time()
                last_sync_day = day

        # 2. Test Trigger
        if test_mode_triggered:
            log_message("TEST MODE: Starting advanced sunset fade.")
            if connect_wifi(): sunset_time = get_sunset_time()
            targetBrightness = SUNSET_TARGET
            Rotary.set(value=int(SUNSET_TARGET))
            automated_fade_active = True
            test_mode_triggered = False

        # 3. Sunset Trigger
        if (hr, mn) == start_time and not automated_fade_active and currBrightness < SUNSET_TARGET:
            log_message(f"SUNSET TRIGGER: Starting {FADE_MINUTES}m fade.")
            targetBrightness = SUNSET_TARGET
            Rotary.set(value=int(SUNSET_TARGET))
            automated_fade_active = True

        # 4. Manual Override
        rot_val = Rotary.value()
        if abs(rot_val - targetBrightness) > 3.0:
            if automated_fade_active:
                log_message(f"OVERRIDE: Knob moved to {rot_val}.")
                automated_fade_active = False
            targetBrightness = rot_val
            if targetBrightness > 0:
                led_on = True
                last_manual_brightness = targetBrightness

        # 5. Fading Logic
        if automated_fade_active:
            # Linear Fade
            if currBrightness < targetBrightness:
                currBrightness += LINEAR_STEP
                if currBrightness > targetBrightness: currBrightness = targetBrightness
            else:
                log_message("Fade Complete.")
                automated_fade_active = False
        else:
            # Asymptotic Manual Fade
            delta = targetBrightness - currBrightness
            if abs(delta) > 0.05:
                currBrightness += delta * MANUAL_STEP
            else:
                currBrightness = targetBrightness

        # 6. Hardware Update (Active Low Mapping)
        duty = int((currBrightness - 0) * (0 - 65535) // (100 - 0) + 65535)
        ledPWM.duty_u16(max(0, min(65535, duty)))

        # 7. Heartbeat Log
        if utime.ticks_diff(utime.ticks_ms(), last_heartbeat) > 10000:
            mode = "AUTO" if automated_fade_active else "MANUAL"
            log_message(f"Mode: {mode} | Curr: {currBrightness:.2f}% | Target: {targetBrightness}%")
            last_heartbeat = utime.ticks_ms()

        utime.sleep_ms(LOOP_MS)

except KeyboardInterrupt:
    ledPWM.duty_u16(65535)
    log_message("System Stopped.")
