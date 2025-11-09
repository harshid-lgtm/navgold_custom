from datetime import datetime
from pytz import timezone

def format_date_time_to_ist(utc_time_str):
    # Parse the UTC time string
    utc_time = datetime.strptime(utc_time_str, '%Y-%m-%dT%H:%M:%SZ')

    # Convert to IST
    utc_zone = timezone('UTC')
    ist_zone = timezone('Asia/Kolkata')

    # Localize the UTC time
    utc_time = utc_zone.localize(utc_time)

    # Convert to IST
    ist_time = utc_time.astimezone(ist_zone)
    return ist_time.strftime('%Y-%m-%d %H:%M:%S')





def format_date_time_to_london(utc_time_str):
    # Parse the UTC time string
    utc_time = datetime.strptime(utc_time_str, '%Y-%m-%dT%H:%M:%SZ')

    # Convert to London time
    utc_zone = timezone('UTC')
    london_zone = timezone('Europe/London')

    # Localize the UTC time
    utc_time = utc_zone.localize(utc_time)

    # Convert to London time
    london_time = utc_time.astimezone(london_zone)
    return london_time.strftime('%Y-%m-%d %H:%M:%S')

