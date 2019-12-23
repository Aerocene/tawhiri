# Copyright 2014 (C) Daniel Richman
#
# This file is part of Tawhiri.
#
# Tawhiri is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Tawhiri is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Tawhiri.  If not, see <http://www.gnu.org/licenses/>.

"""
Open a wind dataset from file by memory-mapping

Datasets downloaded from the NOAA are stored as large binary files that are
memmapped into the predictor process and thereby treated like a huge array.

:class:`Dataset` contains some utility methods to find/list datasets in a
directory, and can open (& create) dataset files.

Note: once opened, the dataset is mmaped as :attr:`Dataset.array`, which by
itself is not particularly useful.  :mod:`tawhiri.interpolate` casts it (via a
memory view) to a pointer in Cython.
"""

from collections import namedtuple
import mmap
import os
import os.path
import signal
import operator
from datetime import datetime
import logging

logger = logging.getLogger("tawhiri.dataset")


# NB: the Sphinx autodoc output for the Dataset class has to be adjusted by
# hand. There is some duplication; see (& update) docs/code/tawhiri.rst.

class Dataset(object):
    """
    A wind dataset

    .. attribute:: array

        A :class:`mmap.mmap` object; the entire dataset mapped into memory.

    .. attribute:: ds_time

        The forecast time of this dataset (:class:`datetime.datetime`).

    """

    forecast_range = range(0, 192 + 3, 3)

    #: The dimensions of the dataset
    #:
    #: Note ``len(axes[i]) == shape[i]``.
    shape = (len(forecast_range), 47, 3, 361, 720)

    # TODO: use the other levels too?
    # {10, 80, 100}m heightAboveGround (u, v)
    #       -- note ground, not mean sea level - would need elevation
    # 0 unknown "planetary boundry layer" (u, v) (first two records)
    # 0 surface "Planetary boundary layer height"
    # {1829, 2743, 3658} heightAboveSea (u, v)

    #: The pressure levels contained in a "pgrb2f" file from the NOAA
    pressures_pgrb2f = [10, 20, 30, 50, 70, 100, 150, 200, 250, 300, 350, 400,
                        450, 500, 550, 600, 650, 700, 750, 800, 850, 900, 925,
                        950, 975, 1000]
    #: The pressure levels contained in a "pgrb2bf" file from the NOAA
    pressures_pgrb2bf = [1, 2, 3, 5, 7, 125, 175, 225, 275, 325, 375, 425,
                         475, 525, 575, 625, 675, 725, 775, 825, 875]

    _axes_type = namedtuple("axes",
                ("hour", "pressure", "variable", "latitude", "longitude"))

    #: The values of the points on each axis: a 5-(named)tuple ``(hour,
    #: pressure variable, latitude, longitude)``.
    #:
    #: For example, ``axes.pressure[4]`` is ``900`` - points in
    #: cells ``dataset.array[a][4][b][c][d]`` correspond to data at 900mb.
    axes = _axes_type(
        forecast_range,
        sorted(pressures_pgrb2f + pressures_pgrb2bf, reverse=True),
        ["height", "wind_u", "wind_v"],
        [x/2.0 for x in range(-180, 180 + 1)],
        [x/2.0 for x in range(0, 720)]
    )

    _listdir_type = namedtuple("dataset_in_row",
                ("ds_time", "suffix", "filename", "path"))

    assert shape == tuple(len(x) for x in axes)

    #: The data type of dataset elements
    element_type = 'float32'
    #: The size in bytes of `element_type`
    element_size = 4    # float32

    #: The size in bytes of the entire dataset
    size = element_size
    for _x in shape:
        size *= _x
    del _x

    #: The filename suffix for "grib mirror" files
    SUFFIX_GRIBMIRROR = '.gribmirror'

    #: The default location of wind data
    DEFAULT_DIRECTORY = '/srv/tawhiri-datasets'

    @classmethod
    def forecast_hours(cls):
        """
        Returns the amount of available forcasting hours

        :rtype: int
        """
        return (cls.shape[0]-1) * 3

    @classmethod
    def setup(cls, forecastHours=192):
        """
        sets up the class for a certain amount of forcasting hours

        :type forecastHours: int
        :param forecastHours: amount of available forecasting hours
        """

        forecast_range = range(0, int(forecastHours) + 3, 3)

        #: The dimensions of the dataset
        #:
        #: Note ``len(axes[i]) == shape[i]``.
        shape = (len(forecast_range), 47, 3, 361, 720)

        axes = cls._axes_type(
            forecast_range,
            sorted(cls.pressures_pgrb2f + cls.pressures_pgrb2bf, reverse=True),
            ["height", "wind_u", "wind_v"],
            [x/2.0 for x in range(-180, 180 + 1)],
            [x/2.0 for x in range(0, 720)]
        )
        assert shape == tuple(len(x) for x in axes)

        # adjust size
        size = cls.element_size
        for _x in shape:
            size *= _x
        del _x

        # set class fields
        cls.size = size
        cls.forecast_range = forecast_range
        cls.shape = shape
        cls.axes = axes

    @classmethod
    def filename(cls, ds_time, directory=DEFAULT_DIRECTORY, suffix=''):
        """
        Returns the filename under which we expect to find a dataset

        ... for forecast time `ds_time`, in `directory` with an optional
        `suffix`

        :type directory: string
        :param directory: directory in which dataset resides/will reside
        :type ds_time: :class:`datetime.datetime`
        :param ds_time: forecast time
        :type suffix: string
        :rtype: string
        """

        ds_time_str = ds_time.strftime("%Y%m%d%H")
        return os.path.join(directory, ds_time_str + suffix)

    @classmethod
    def listdir(cls, directory=DEFAULT_DIRECTORY, only_suffices=None):
        """
        Scan for datasets in `directory`

        ... with filenames matching those generated by :meth:`filename`
        and (optionally) filter by only looking for certian suffices.

        :type directory: string
        :param directory: directory to search in
        :type only_suffices: set
        :param only_suffices: if not ``None``, only return results with a
                              suffix contained in this set
        :rtype: (named) tuples ``(dataset time, suffix, filename, full path)``
        """

        for filename in os.listdir(directory):
            if len(filename) < 10:
                continue

            ds_time_str = filename[:10]
            try:
                ds_time = datetime.strptime(ds_time_str, "%Y%m%d%H")
            except ValueError:
                pass
            else:
                suffix = filename[10:]
                if only_suffices and suffix not in only_suffices:
                    continue

                yield cls._listdir_type(ds_time, suffix, filename,
                                        os.path.join(directory, filename))

    cached_latest = None

    # prune_latest is registered as the signal handler for SIGALRM at the
    # bottom of the file.
    @classmethod
    def prune_latest(cls, signum, stack_frame):
        cls.cached_latest = None

    @classmethod
    def open_latest(cls, directory=DEFAULT_DIRECTORY, persistent=False, determineTime=True):
        """
        Find the most recent datset in `directory`, and open it

        :type directory: string
        :param directory: directory to search
        :type persistent: bool
        :param persistent: should the latest dataset be cached, and re-used?
        :type determineTime: bool
        :param determineTime: should available forecast hours should be determined by filesize?
        :rtype: :class:`Dataset`
        """

        datasets = Dataset.listdir(directory, only_suffices=('', ))
        latest = sorted(datasets, reverse=True)[0].ds_time

        cached = cls.cached_latest
        valid = cached and \
                cached.ds_time == latest and \
                cached.directory == directory

        if valid:
            if persistent:
                # Refresh countdown
                signal.alarm(60)

            return cls.cached_latest
        else:
            ds = Dataset(latest, directory=directory, determineTime=determineTime)

            if persistent:
                # Start the countdown
                signal.alarm(60)
                # note, this creates a ref cycle.
                cls.cached_latest = ds

            return ds

    def __init__(self, ds_time, directory=DEFAULT_DIRECTORY, new=False, determineTime=True):
        """
        Open the dataset file for `ds_time`, in `directory`

        :type directory: string
        :param directory: directory containing the dataset
        :type ds_time: :class:`datetime.datetime`
        :param ds_time: forecast time
        :type new: bool
        :param new: should a new (blank) dataset be created (overwriting
                    any file that happened to already be there), or should
                    an existing dataset be opened?
        :type determineTime: bool
        :param determineTime: should available forecast hours should be determined by filesize?
        """

        self.directory = directory
        self.ds_time = ds_time
        self.new = new

        self.fn = self.filename(self.ds_time, directory=self.directory)

        prot = mmap.PROT_READ
        flags = mmap.MAP_SHARED

        if new:
            mode = "w+b"
            prot |= mmap.PROT_WRITE
            msg = "truncate and write"
        else:
            mode = "rb"
            msg = "read"

        logger.info("Opening dataset %s %s (%s)", self.ds_time, self.fn, msg)

        with open(self.fn, mode) as f:
            if new:
                f.seek(self.size - 1)
                f.write(b"\0")
            else:
                f.seek(0, 2)
                sz = f.tell()

                if determineTime:
                    # use filesize to determine available forecasting hours
                    time = sz / self.element_size / self.shape[1] / self.shape[2] / self.shape[3] / self.shape[4]
                    self.setup((time-1)*3)

                if sz != self.size:
                    raise ValueError("Dataset should be {0} bytes (was {1})"
                                        .format(self.size, sz))
            f.seek(0, 0)

            self.array = mmap.mmap(f.fileno(), 0, prot=prot, flags=flags)

    def __del__(self):
        self.close()

    def close(self):
        """
        Close the dataset

        This deletes :attr:`array`, thereby releasing (a) reference to it.
        Note that other objects may very well hold a reference to the array,
        keeping it open.

        (The file descriptor is closed as soon as the dataset is mapped.)
        """

        if hasattr(self, 'array'):
            logger.info("Closing dataset %s %s", self.ds_time, self.fn)
            del self.array


signal.signal(signal.SIGALRM, Dataset.prune_latest)
