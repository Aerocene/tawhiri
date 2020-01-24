import sys
from os.path import abspath, split, join
sys.path.append(join(split(abspath(__file__))[0], '..'))

import time
import itertools
from datetime import datetime, timedelta
import json
import calendar

from suntime import Sun, SunTimeException

from tawhiri import solver, models, kml
from tawhiri.dataset import Dataset as WindDataset
from ruaumoko import Dataset as ElevationDataset
from tawhiri.warnings import WarningCounts

lat0 = 52.5563
lng0 = 360 - 3.1970
alt0 = 0.0
offsetDays = 3
useSunrise = False
timeResolution = 4000

wind = WindDataset.open_latest()
warningcounts = WarningCounts()

#
new_datetime = wind.ds_time

# check sunrise
if useSunrise == True:
    new_datetime = Sun(lat0, lng0).get_sunrise_time(wind.ds_time.date())

    if new_datetime.hour < wind.ds_time.hour:
        # can only start next day
        new_datetime = Sun(lat0, lng0).get_sunrise_time(wind.ds_time.date() + timedelta(days=1))


# add offset days
new_datetime = new_datetime + timedelta(days=offsetDays)

ds_end = wind.ds_time + timedelta(hours=wind.forecast_hours(), minutes=-1)
ds_end_ts = calendar.timegm(ds_end.timetuple())

#t0 = calendar.timegm(datetime(2020, 1, 7, 21).timetuple())
#tE = calendar.timegm(datetime(2014, 2, 20, 6, 1).timetuple())

t0 = calendar.timegm(new_datetime.timetuple())
tE = ds_end_ts

float_alt = 5500

print "wind: ", wind.ds_time
print "start (local): ", datetime.fromtimestamp(t0)
print "end: ", ds_end
print "time resolution: ", timeResolution


stages = models.float_profile(2.0, float_alt, tE, wind, warningcounts)
rise, float = solver.solve(t0, lat0, lng0, alt0, stages, timeResolution)

assert rise[-1] == float[0]

with open("test_prediction_data.js", "w") as f:
    f.write("var data = ")
    json.dump([(lat, lon) for _, lat, lon, _ in rise + float], f, indent=4)
    f.write(";\n")

markers = [
    {'name': 'launch', 'description': 'TODO', 'point': rise[0]},
    {'name': 'reached float', 'description': 'TODO', 'point': float[0]}
]

kml.kml([rise, float], markers, 'test_prediction.kml')
