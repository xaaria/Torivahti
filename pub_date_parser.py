# testi
import re
import datetime as dt
from datetime import timedelta
from pytz import timezone

def get_timestamp(time_str, tz_string="Europe/Helsinki"):

  """ 
    Returns datetime.datetime -object or None (an error occured)

    Get timestamp from string. Function ignores string older than 1 day
      - 'Tänään hh:mm'
      - 'Eilen hh:mm'
      - 'Tam 1 hh:mm'
  """

  tz        = timezone(tz_string)
  pub_time  = dt.datetime.now(tz)
  time_str  = time_str.strip().lower()
  
  # print(time_str)

  # Get all numbers. Number might be date num. (for example '10 Tam 12:34') => ['10','12','34']
  try:
    ts = list(map(int, re.findall('\d{1,2}', time_str)))[-2:]
    
    if(len(ts) == 2):
      pub_time = pub_time.replace(hour=ts[0]).replace(minute=ts[1]).replace(second=59)
    
  except Exception as e:
    print("Parsing time failed: " + e.__str__())
    return None


  # Get day
  if re.search("tänään", time_str) != None:
    #print("\tToday!")
    pub_time = pub_time.replace(day=dt.datetime.now(tz).day)
  elif re.search("eilen", time_str) != None:
    #print("\tYesturday")
    pub_time = pub_time - timedelta(days=1)
  else:
    # days_ago, TODO: calculate
    #print("\tDay was not recognized!")
    return None

  #print(str.format("\t>> {}\n", pub_time))
  
  return pub_time


"""
get_timestamp("Tänään 12:34")
get_timestamp("Eilenn 20:30")
get_timestamp("Eilenn 23:59")
get_timestamp("Eilenn 03.11")
get_timestamp("Eilenn 26:59")
get_timestamp("eilen ---")
get_timestamp("tänään 10.00")
get_timestamp("TÄNÄÄN 10.0")
get_timestamp("  TÄNÄÄN ")
get_timestamp("\n  TÄNÄÄN ")
get_timestamp("\n  TÄNääN ")
get_timestamp("Eil\ten")
get_timestamp("Jou 12 10:20")
"""