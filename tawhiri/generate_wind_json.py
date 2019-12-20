"""
Generate wind json data to be used in floatpredictor wind visualisation.

This scripts generates wind vectors for a icosahedron geometry
with detail-level of 6 (40962 vertices).

geo6.json contains latitude, longitude tuples for each vertice
and is used to lookup wind directions at that location at a given altitude.

output is a json-file containing a list of points (x,y,z tuples)
on a sphere with a given radius.
the radius defaults to 200, the earth radius used in floatpredictor.

output:
{'data': [array of x,y,z tuples]}
"""

import datetime
import os
import json
import math
import shutil
import traceback
import logging

from tawhiri import interpolate
from tawhiri.dataset import Dataset as WindDataset
from tawhiri.warnings import WarningCounts

# setup logger
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__file__)

# paths
dataset_path = os.getenv('WIND_DATASET_DIR', WindDataset.DEFAULT_DIRECTORY);
output_path = os.getenv('WIND_OUTPUT_DIR', WindDataset.DEFAULT_DIRECTORY);

# setup wind dataset
wind = WindDataset.open_latest(directory=dataset_path)
warningcounts = WarningCounts()
# make wind interpolator
get_wind = interpolate.make_interpolator(wind, warningcounts)
# how many days can our dataset forecast?
forecast_days = int(wind.forecast_hours() / 24)

# geo6 contains lat,lng for our lookup
geo6_json = json.load(open('geo6.json'))
vertices=iter(geo6_json['vertices'])


def latLonToXYZ(lat,lon,radius=200):
    theta=(lon+180)*(math.pi/180)
    phi   = (90-lat)*(math.pi/180)
    x = -((radius) * math.sin(phi)*math.cos(theta))
    z = ((radius) * math.sin(phi)*math.sin(theta))
    y = ((radius) * math.cos(phi))
    return (x,y,z)

def generateWindJson(time, alt):
    """
    generates wind json for a certain forcasting hour and altitude

    :type time: int
    :param time: forecasting hour to get wind data
    :type alt: int
    :param alt: altitude in meters
    """

    logger.info("generating wind data for altitude: " + str(alt) + " time: " + str(time))

    data=[]
    for lat,lon in zip(vertices,vertices):
        x,y,z=latLonToXYZ(lat,lon)

        if lat >= 90.0:
            lat = 89.99

        if lat <= -90.0:
            lat = -89.99

        u, v = get_wind(time, lat, lon, alt)
        x1,y1,z1=latLonToXYZ(lat + v*0.2, lon + u*0.2)
        data.extend([round(x1-x,2),round(y1-y,2),round(z1-z,2)])

    if not os.path.isdir("data/"+str(alt)):
        os.mkdir("data/"+str(alt))

    with open("data/"+str(alt)+"/"+str(time)+".json", 'w') as outfile:
        json.dump({'data':data}, outfile, separators=(',',':'))

try:
    # make sure paths exist
    if not os.path.isdir(output_path):
        os.makedirs(output_path)

    if not os.path.isdir("data"):
        os.mkdir("data")

    # available heights in meters
    for alt in [100, 1500, 5500, 10000, 16000, 21500, 26500]:
        for i in range(0,8*forecast_days,8):
            generateWindJson(i*3, alt);

    # move data into place
    for src_dir, dirs, files in os.walk("data"):
        dst_dir = src_dir.replace("data", os.path.join(output_path, "data"), 1)
        if not os.path.exists(dst_dir):
            os.makedirs(dst_dir)
        for file_ in files:
            src_file = os.path.join(src_dir, file_)
            dst_file = os.path.join(dst_dir, file_)
            if os.path.exists(dst_file):
                # in case of the src and dst are the same file
                if os.path.samefile(src_file, dst_file):
                    continue
                os.remove(dst_file)
            shutil.move(src_file, dst_dir)

    # remove data folder
    shutil.rmtree("data")

except Exception as e:
    # print("Error generating json: ", traceback.format_exc())
    logger.exception(e)
    exit(1)
