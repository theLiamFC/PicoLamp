import utime
import network, ntptime, urequests

def map_value(value, in_min, in_max, out_min, out_max):
    """Map a value from one range to another"""
    return int((value - in_min) * (out_max - out_min) // (in_max - in_min) + out_min)

def read_temp(TempSensor):    
    # Convert ADC value to voltage
    voltage = TempSensor.read_u16() * (3.3 / 65535)
    
    # Calculate temperature using the voltage
    temperature = 27 - (voltage - 0.706) / 0.001721
        
    # Round the temperature to one decimal place
    return round(temperature, 1)

def get_local_time():
    timestamp = utime.time()

    utc_tuple = utime.localtime(timestamp)
    utc_string = "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}+00:00".format(
        utc_tuple[0], utc_tuple[1], utc_tuple[2], utc_tuple[3], utc_tuple[4], utc_tuple[5])
    pacific_str = utc_to_pacific_time(utc_string)

    hour, minute, second = map(int, pacific_str.split(":"))

    return (utc_tuple[0], utc_tuple[1], utc_tuple[2], hour, minute, second, utc_tuple[6], utc_tuple[7])

def connect_wifi(ssid,password):
    # Initialize WLAN interface
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    utime.sleep(1)

    # Connect to WiFi network
    ssid = 'Stanford'
    password = ''
    try:
        wlan.connect(ssid, password)
        # Wait for connection with timeout
        max_attempts = 15
        attempt = 0
        while not wlan.isconnected() and attempt < max_attempts:
            print("Attempting to connect...")
            utime.sleep(1)
            attempt += 1
        if wlan.isconnected():
            print(f"Connected to {ssid}")
            print("Network config:", wlan.ifconfig())
            ntptime.settime()
            print(f"Time set to: {get_local_time()}")
            return True
        else:
            print("Failed to connect to WiFi.")
            return False
    except Exception as e:
        print(f"Connection error: {e}")
        return False

def utc_to_pacific_time(utc_string):
    # Example input: "2025-09-22T13:55:37+00:00"
    time_str = utc_string[11:19]  # "13:55:37"
    hour = int(time_str[0:2])
    minute = int(time_str[3:5])
    second = int(time_str[6:8])

    # PDT offset is UTC-7
    hour -= 7
    if hour < 0:
        hour += 24  # Optional: date wrap-around not handled here

    new_time = "{:02d}:{:02d}:{:02d}".format(hour, minute, second)
    return new_time

def get_sunrise_sunset_pacific(lat, lng):
    api_url = 'http://api.sunrise-sunset.org/json?lat={}&lng={}&formatted=0'.format(lat, lng)
    try:
        response = urequests.get(api_url)
        data = response.json()
        response.close()
    except Exception as e:
        return f"HTTP error: {e}"

    if data["status"] != "OK":
        return f"API error: {data['status']}"

    sunrise_utc = data["results"]["sunrise"]
    sunset_utc = data["results"]["sunset"]

    sunrise_local = list(map(int, utc_to_pacific_time(sunrise_utc).split(":")))
    sunset_local = list(map(int, utc_to_pacific_time(sunset_utc).split(":")))
    
    print(f"Sunrise today is at {sunrise_local[0]}:{sunrise_local[1]}")
    print(f"Sunset today is at {sunset_local[0]}:{sunset_local[1]}")

    return (sunrise_local[0],sunrise_local[1]), (sunset_local[0],sunset_local[1])

def increment_time(inc_hours, inc_minutes):
    _, _, _, curr_hour, curr_minutes, _, _, _ = get_local_time()
    
    hours = curr_hour + inc_hours
    minutes = curr_minutes + inc_minutes
    if minutes >= 60:
        hours += 1
        minutes -= 60
    if hours >= 24:
        hours -= 24

    return (hours, minutes, 0)

def get_curr_fade(start, end, fade_on, max=0.8):
    _, _, _, curr_hour, curr_minute, curr_second, _, _ = get_local_time()

    start_hour, start_minute, start_second = start
    end_hour, end_minute, end_second = end

    # Convert all times to seconds since midnight
    curr_total_sec = (curr_hour * 3600) + (curr_minute * 60) + curr_second
    start_total_sec = (start_hour * 3600) + (start_minute * 60) + start_second
    end_total_sec = (end_hour * 3600) + (end_minute * 60) + end_second

    # Handle wrap-around (e.g., end < start)
    if end_total_sec < start_total_sec:
        end_total_sec += 24 * 3600
        if curr_total_sec < start_total_sec:
            curr_total_sec += 24 * 3600

    duration = end_total_sec - start_total_sec
    elapsed = curr_total_sec - start_total_sec

    progress = elapsed / duration
    if fade_on:
        brightness = 1 - progress
    else:
        brightness = progress

    return int(brightness * 100)
