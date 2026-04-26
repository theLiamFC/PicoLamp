from machine import Pin, PWM, ADC
import utime
import network
import urequests
import ntptime
from rotary_irq_rp2 import RotaryIRQ

#################################################################
# CONFIGURATION & CONSTANTS
#################################################################

# Location: Stanford, CA
LAT = 37.4275
LNG = -122.1697
UTC_OFFSET = -7  # PDT (Adjust to -8 for PST)

# Timing & Fading
LOOP_MS = 10
FADE_MINUTES = 30
MANUAL_STEP = 0.05
SUNSET_TARGET = 60.0
SUNSET_STEP = SUNSET_TARGET / (FADE_MINUTES * 60 * (1000 / LOOP_MS))

# WiFi Credentials (filled from your WIFI_CONFIG context)
SSID = 'Stanford'
PASSWORD = ''

#################################################################
# HARDWARE SETUP
#################################################################

# PWM LED Setup (Active Low / Inverted logic)
ledPWM = PWM(Pin(11))
ledPWM.freq(200)

# Rotary Encoder
Rotary = RotaryIRQ(
    pin_num_clk=2,
    pin_num_dt=3,
    min_val=0,
    max_val=100,
    reverse=True,
    range_mode=RotaryIRQ.RANGE_BOUNDED,
    pull_up=True
)

# Button with Pull Down
Button = Pin(4, Pin.IN, Pin.PULL_DOWN)

#################################################################
# STATE VARIABLES
#################################################################

currBrightness = 0.0
targetBrightness = 0.0
last_manual_brightness = 100.0
led_on = False

# Automation Flags
sunset_time = (19, 0)  # Default fallback
automated_fade_active = False
last_sync_day = -1

# Input Tracking
lastButtonTime = 0
buttonStartTime = 0
LONG_PRESS_MS = 1000
test_mode_triggered = False

#################################################################
# HELPER FUNCTIONS
#################################################################

def connect_wifi():
    print("Connecting to WiFi...")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    
    # Non-blocking wait for 10 seconds
    for _ in range(10):
        if wlan.isconnected():
            print("Connected!")
            try:
                ntptime.settime()
                return True
            except:
                return False
        utime.sleep(1)
    print("WiFi Failed.")
    return False

def get_sunset_time():
    print("Fetching Sunset API...")
    try:
        url = f"http://api.sunrise-sunset.org/json?lat={LAT}&lng={LNG}&formatted=0"
        res = urequests.get(url).json()
        # Parse ISO8601: 2026-04-26T02:45:12+00:00
        t_str = res['results']['sunset'].split('T')[1]
        utc_h, utc_m = int(t_str[:2]), int(t_str[3:5])
        
        local_h = (utc_h + UTC_OFFSET) % 24
        print(f"Sunset synced: {local_h:02d}:{utc_m:02d}")
        return (local_h, utc_m)
    except Exception as e:
        print(f"API Error: {e}")
        return (19, 15) # Safety fallback

def button_handler(pin):
    global targetBrightness, led_on, last_manual_brightness
    global lastButtonTime, buttonStartTime, test_mode_triggered
    
    current_time = utime.ticks_ms()
    if pin.value() == 1:  # Pressed
        buttonStartTime = current_time
    else:                 # Released
        press_duration = utime.ticks_diff(current_time, buttonStartTime)
        
        if press_duration > LONG_PRESS_MS:
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

# Attach Interrupt
Button.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=button_handler)

#################################################################
# MAIN OPERATIONAL LOOP
#################################################################

# Initial Sync
if connect_wifi():
    sunset_time = get_sunset_time()

print("System Ready. Long-press knob to test Sunset.")

try:
    # --- Additional Variable for Debugging ---
    last_heartbeat = 0

    while True:
        lt = utime.localtime()
        day, hr, mn, sec = lt[2], lt[3], lt[4], lt[5]

        # 1. Daily Sync
        if day != last_sync_day and hr == 3:
            if connect_wifi():
                sunset_time = get_sunset_time()
                last_sync_day = day

        # 2. Test Mode Trigger
        if test_mode_triggered:
            print("\n[!] TEST TRIGGERED: Forcing 30-min fade now.")
            if connect_wifi(): 
                sunset_time = get_sunset_time()
            
            currBrightness = 0.0
            targetBrightness = SUNSET_TARGET
            Rotary.set(value=SUNSET_TARGET)
            automated_fade_active = True
            test_mode_triggered = False
            print(f"[*] State: Target={targetBrightness}, Automated={automated_fade_active}")

        # 3. Scheduled Sunset Trigger
        if (hr, mn) == sunset_time and not automated_fade_active and currBrightness < SUNSET_TARGET:
            print(f"\n[!] SUNSET REACHED ({hr:02d}:{mn:02d}): Starting auto-fade.")
            targetBrightness = SUNSET_TARGET
            Rotary.set(value=SUNSET_TARGET)
            automated_fade_active = True

        # 4. Manual Knob Control & Priority Override
        rot_val = Rotary.value()
        if abs(rot_val - targetBrightness) > 3:
            # User moved the knob
            targetBrightness = rot_val
            if automated_fade_active:
                print(f"\n[!] OVERRIDE: Knob moved to {rot_val}. Automation killed.")
                automated_fade_active = False
            
            if targetBrightness > 0:
                led_on = True
                last_manual_brightness = targetBrightness

        # 5. Smooth Fade Logic
        if automated_fade_active:
            # LINEAR FADE: Add a constant small amount every loop
            if currBrightness < targetBrightness:
                currBrightness += SUNSET_STEP
                
                # Prevent overshoot
                if currBrightness > targetBrightness:
                    currBrightness = targetBrightness
            else:
                print(f"\n[✓] DONE: Automated linear fade reached {targetBrightness}%.")
                automated_fade_active = False
        else:
            # SNAPPY MANUAL FADE: Keep the asymptotic "snap" for the knob
            delta = targetBrightness - currBrightness
            if abs(delta) > 0.1:
                currBrightness += delta * MANUAL_STEP
            else:
                currBrightness = targetBrightness

        # 6. PWM Update
        duty = int((currBrightness - 0) * (0 - 65535) // (100 - 0) + 65535)
        ledPWM.duty_u16(max(0, min(65535, duty)))

        # 7. Debug Heartbeat (Prints every 5 seconds)
        if utime.ticks_diff(utime.ticks_ms(), last_heartbeat) > 5000:
            mode = "AUTO-FADE" if automated_fade_active else "MANUAL"
            print(f"[{hr:02d}:{mn:02d}:{sec:02d}] Mode: {mode} | Curr: {currBrightness:.2f}% | Target: {targetBrightness}%")
            last_heartbeat = utime.ticks_ms()

        utime.sleep_ms(LOOP_MS)

except KeyboardInterrupt:
    ledPWM.duty_u16(65535) # Turn off
    print("Interrupted")
