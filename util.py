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

def is_dst(t):
    # A simple DST check for North America
    # DST runs from the second Sunday in March to the first Sunday in November.
    year, month, day, _, _, _, weekday, _ = t
    if month < 3 or month > 11:
        return False
    if month > 3 and month < 11:
        return True
    
    # Find the second Sunday in March
    first_day_of_march_weekday = utime.localtime(utime.mktime((year, 3, 1, 0, 0, 0, 0, 0)))[6]
    second_sunday_march = 14 - (first_day_of_march_weekday + 1) % 7

    # Find the first Sunday in November
    first_day_of_nov_weekday = utime.localtime(utime.mktime((year, 11, 1, 0, 0, 0, 0, 0)))[6]
    first_sunday_november = 7 - (first_day_of_nov_weekday + 1) % 7

    if month == 3: return day >= second_sunday_march
    if month == 11: return day < first_sunday_november
    return False

def get_local_time():
    timestamp = utime.time()
    return utime.localtime(timestamp - (7 * 3600 if is_dst(utime.localtime(timestamp)) else 8 * 3600))

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
    # Convert UTC time string to a local time tuple
    # e.g., "2024-05-21T01:55:37+00:00"
    # MicroPython's utime doesn't have strptime, so we parse manually.
    s = utc_string[:19]
    year = int(s[0:4])
    month = int(s[5:7])
    day = int(s[8:10])
    hour = int(s[11:13])
    minute = int(s[14:16])
    second = int(s[17:19])
    t = (year, month, day, hour, minute, second, 0, 0) # weekday and yearday are not needed for mktime
    utc_seconds = utime.mktime(t)
    local_seconds = utc_seconds - (7 * 3600 if is_dst(t) else 8 * 3600)
    return utime.localtime(local_seconds)

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

    sunrise_local_tuple = utc_to_pacific_time(sunrise_utc)
    sunset_local_tuple = utc_to_pacific_time(sunset_utc)
    
    print(f"Sunrise today is at {sunrise_local_tuple[3]:02d}:{sunrise_local_tuple[4]:02d}")
    print(f"Sunset today is at {sunset_local_tuple[3]:02d}:{sunset_local_tuple[4]:02d}")

    return (sunrise_local_tuple[3],sunrise_local_tuple[4]), (sunset_local_tuple[3],sunset_local_tuple[4])

def get_time_offset(time_tuple, offset_minutes):
    """Calculates a new time by adding or subtracting minutes from a given time tuple (h, m)."""
    h, m = time_tuple
    total_minutes = h * 60 + m + offset_minutes
    
    # Handle wrap-around for days
    total_minutes = total_minutes % (24 * 60)
    
    new_h = total_minutes // 60
    new_m = total_minutes % 60
    return (new_h, new_m)

def get_curr_fade(start, end):
    _, _, _, curr_hour, curr_minute, curr_second, _, _ = get_local_time()

    start_hour, start_minute, start_second = start
    end_hour, end_minute, end_second = end

    # Convert all times to seconds since midnight
    curr_total_sec = (curr_hour * 3600) + (curr_minute * 60) + curr_second
    start_total_sec = (start_hour * 3600) + (start_minute * 60) + start_second
    end_total_sec = (end_hour * 3600) + (end_minute * 60) + end_second

    # Handle wrap-around (e.g., end < start)
    if end_total_sec < start_total_sec:
        if curr_total_sec < start_total_sec:
            curr_total_sec += 24 * 3600
        end_total_sec += 24 * 3600

    duration = end_total_sec - start_total_sec
    elapsed = curr_total_sec - start_total_sec

    if duration > 0 and elapsed >= 0:
        return elapsed / duration
    return 1.0 if elapsed >= duration else 0.0
