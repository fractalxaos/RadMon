#!/usr/bin/python -u
# The -u option above turns off block buffering of python output. This 
# assures that each error message gets individually printed to the log file.
#
# Module: radmonAgent.py
#
# Description: This module acts as an agent between the radiation monitoring
# device and Internet web services.  The agent periodically sends an http
# request to the radiation monitoring device and processes the response from
# the device and performs a number of operations:
#     - conversion of data items
#     - update a round robin (rrdtool) database with the radiation data
#     - periodically generate graphic charts for display in html documents
#     - write the processed weather data to a JSON file for use by html
#       documents
#
# Copyright 2015 Jeff Owrey
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see http://www.gnu.org/license.
#
# Revision History
#   * v20 released 15 Sep 2015 by J L Owrey; first release
#   * v21 released 27 Nov 2017 by J L Owrey; bug fixes; updates
#   * v22 released 03 Mar 2018 by J L Owrey; improved code readability;
#         improved radmon device offline status handling
#   * v23 released 16 Nov 2018 by J L Owrey: improved fault handling
#         and data conversion

_MIRROR_SERVER = False

import os
import urllib2
import sys
import signal
import subprocess
import multiprocessing
import time
import calendar

_USER = os.environ['USER']

   ### DEFAULT RADIATION MONITOR URL ###

# ip address of radiation monitoring device
_DEFAULT_RADIATION_MONITOR_URL = "{your radiation monitor url}"
# url if this is a mirror server
_PRIMARY_SERVER_URL = "{your primary server url}" \
                      "/radmon/dynamic/radmonInputData.dat"

    ### FILE AND FOLDER LOCATIONS ###

# folder for containing dynamic data objects
_DOCROOT_PATH = "/home/%s/public_html/radmon/" % _USER
# folder for charts and output data file
_CHARTS_DIRECTORY = _DOCROOT_PATH + "dynamic/"
# location of data input file
_INPUT_DATA_FILE = _DOCROOT_PATH + "dynamic/radmonInputData.dat"
# location of data output file
_OUTPUT_DATA_FILE = _DOCROOT_PATH + "dynamic/radmonOutputData.js"
# database that stores weather data
_RRD_FILE = "/home/%s/database/radmonData.rrd" % _USER

    ### GLOBAL CONSTANTS ###

# max number of failed data requests allowed
_MAX_FAILED_DATA_REQUESTS = 2
# interval in seconds between data requests to radiation monitor
_DEFAULT_DATA_REQUEST_INTERVAL = 5
# defines how often the charts get updated in seconds
_CHART_UPDATE_INTERVAL = 300
# defines how often the database gets updated
_DATABASE_UPDATE_INTERVAL = 30
# number seconds to wait for a response to HTTP request
_HTTP_REQUEST_TIMEOUT = 3
# standard chart width in pixels
_CHART_WIDTH = 600
# standard chart height in pixels
_CHART_HEIGHT = 150

   ### GLOBAL VARIABLES ###

# turn on or off of verbose debugging information
debugOption = False
verboseDebug = False

# The following two items are used for detecting system faults
# and radiation monitor online or offline status.

# count of failed attempts to get data from radiation monitor
failedUpdateCount = 0
# detected status of radiation monitor device
stationOnline = True

# status of reset command to radiation monitor
remoteDeviceReset = False
# ip address of radiation monitor
radiationMonitorUrl = _DEFAULT_RADIATION_MONITOR_URL
# web update frequency
dataRequestInterval = _DEFAULT_DATA_REQUEST_INTERVAL

  ###  PRIVATE METHODS  ###

def getTimeStamp():
    """
    Set the error message time stamp to the local system time.
    Parameters: none
    Returns: string containing the time stamp
    """
    return time.strftime( "%m/%d/%Y %T", time.localtime() )
##end def

def setStatusToOffline():
    """Set the detected status of the radiation monitor to
       "offline" and inform downstream clients by removing input
       and output data files.
       Parameters: none
       Returns: nothing
    """
    global stationOnline

    # Inform downstream clients by removing input and output
    # data files.
    if os.path.exists(_INPUT_DATA_FILE):
        os.remove(_INPUT_DATA_FILE)
    if os.path.exists(_OUTPUT_DATA_FILE):
       os.remove(_OUTPUT_DATA_FILE)

    # If the radiation monitor was previously online, then send
    # a message that we are now offline.
    if stationOnline:
        print '%s radiation monitor offline' % getTimeStamp()
    stationOnline = False
##end def

def terminateAgentProcess(signal, frame):
    """Send a message to log when the agent process gets killed
       by the operating system.  Inform downstream clients
       by removing input and output data files.
       Parameters:
           signal, frame - dummy parameters
       Returns: nothing
    """
    print '%s terminating radmon agent process' % \
              (getTimeStamp())

    # Inform downstream clients by removing input and output
    # data files.
    if os.path.exists(_OUTPUT_DATA_FILE):
        os.remove(_OUTPUT_DATA_FILE)
    if os.path.exists(_INPUT_DATA_FILE):
        os.remove(_INPUT_DATA_FILE)
    sys.exit(0)
##end def

  ###  PUBLIC METHODS  ###

def getRadiationData():
    """Send http request to radiation monitoring device.  The
       response from the device contains the radiation data as
       unformatted ascii text.
       Parameters: none 
       Returns: a string containing the radiation data if successful,
                or None if not successful
    """
    global remoteDeviceReset

    if _MIRROR_SERVER:
        sUrl = _PRIMARY_SERVER_URL
    else:
        sUrl = radiationMonitorUrl
        if remoteDeviceReset:
            sUrl += "/reset" # reboot the radiation monitor
        else:
            sUrl += "/rdata" # request data from the monitor

    try:
        conn = urllib2.urlopen(sUrl, timeout=_HTTP_REQUEST_TIMEOUT)

        # Format received data into a single string.
        content = ""
        for line in conn:
            content += line.strip()
        del conn

    except Exception, exError:
        # If no response is received from the device, then assume that
        # the device is down or unavailable over the network.  In
        # that case return None to the calling function.
        if debugOption:
            print "http error: %s" % exError
        return None

    return content
##end def

def parseDataString(sData, dData):
    """Parse the radiation data JSON string from the radiation 
       monitoring device into its component parts.  
       Parameters:
           sData - the string containing the data to be parsed
           dData - a dictionary object to contain the parsed data items
       Returns: True if successful, False otherwise
    """
    try:
        sTmp = sData[2:-2]
        lsTmp = sTmp.split(',')
    except Exception, exError:
        print "%s parseDataString: %s" % (getTimeStamp(), exError)
        return False

    # Load the parsed data into a dictionary for easy access.
    for item in lsTmp:
        if "=" in item:
            dData[item.split('=')[0]] = item.split('=')[1]
    dData['status'] = 'online'

    # Verfy the expected number of data items have been received.
    if len(dData) != 6:
        print "%s parse failed: corrupted data string" % getTimeStamp()
        return False;

    return True
##end def

def convertData(dData):
    """Convert individual radiation data items as necessary.
       Parameters:
           dData - a dictionary object containing the radiation data
       Returns: True if successful, False otherwise
    """
    try:
        # Convert the UTC timestamp provided by the radiation monitoring
        # device to epoch local time in seconds.
        ts_utc = time.strptime(dData['UTC'], "%H:%M:%S %m/%d/%Y")
        epoch_local_sec = calendar.timegm(ts_utc)
        dData['ELT'] = epoch_local_sec

        # Uncomment the code line below to use a timestamp generated by the
        # requesting server (this) instead of the timestamp provided by the
        # radiation monitoring device.  Using the server generated timestamp
        # prevents errors that occur when the radiation monitoring device
        # fails to synchronize with a valid NTP time server.
        #dData['ELT'] = time.time()
        
        dData['Mode'] = dData['Mode'].lower()
        dData['uSvPerHr'] = '%.2f' % float(dData.pop('uSv/hr'))

    except Exception, exError:
        print "%s data conversion failed: %s" % (getTimeStamp(), exError)
        return False

    return True
##end def

def writeOutputDataFile(dData):
    """Write radiation data items to the output data file, formatted as 
       a Javascript file.  This file may then be accessed and used by
       by downstream clients, for instance, in HTML documents.
       Parameters:
           dData - a dictionary object containing the data to be written
                   to the output data file
       Returns: True if successful, False otherwise
    """
    # Set date to current time and data
    dData['date'] = time.strftime("%m/%d/%Y %T", time.localtime(dData['ELT']))

    # Remove unnecessary data items.
    dTemp = dict(dData)
    dTemp.pop('ELT')
    dTemp.pop('UTC')
    
    # Format the weather data as string using java script object notation.
    sData = '[{'
    for key in dTemp:
        sData += '\"%s\":\"%s\",' % (key, dData[key])
    sData = sData[:-1] + '}]\n'

    # Write the string to the output data file for use by html documents.
    try:
        fc = open(_OUTPUT_DATA_FILE, "w")
        fc.write(sData)
        fc.close()
    except Exception, exError:
        print "%s writeOutputDataFile: %s" % (getTimeStamp(), exError)
        return False

    return True
## end def

def writeInputDataFile(sData):
    """Write raw data from radiation monitor to the input data file.
       This file may then be accessed by downstream mirror servers.
       Parameters:
           sData - a string object containing the raw data from
                   the radiation monitor
       Returns: True if successful, False otherwise
    """
    sData += "\n"
    try:
        fc = open(_INPUT_DATA_FILE, "w")
        fc.write(sData)
        fc.close()
    except Exception, exError:
        print "%s writeInputDataFile: %s" % (getTimeStamp(), exError)
        return False

    return True
##end def

def setStationStatus(updateSuccess):
    """Detect if radiation monitor is offline or not available on
       the network. After a set number of attempts to get data
       from the monitor set a flag that the station is offline.
       Parameters:
           updateSuccess - a boolean that is True if data request
                           successful, False otherwise
       Returns: nothing
    """
    global failedUpdateCount, stationOnline

    if updateSuccess:
        failedUpdateCount = 0
        # Set status and send a message to the log if the station was
        # previously offline and is now online.
        if not stationOnline:
            print '%s radiation monitor online' % getTimeStamp()
            stationOnline = True
        if debugOption:
            print 'radiation update successful'
    else:
        # The last attempt failed, so update the failed attempts
        # count.
        failedUpdateCount += 1
        if debugOption:
           print 'radiation update failed'

    if failedUpdateCount >= _MAX_FAILED_DATA_REQUESTS:
        # Max number of failed data requests, so set
        # monitor status to offline.
        setStatusToOffline()
##end def


def updateDatabase(dData):
    """
    Update the rrdtool database by executing an rrdtool system command.
    Format the command using the data extracted from the radiation
    monitor response.   
    Parameters: dData - dictionary object containing data items to be
                        written to the rr database file
    Returns: True if successful, False otherwise
    """
    global remoteDeviceReset

    # The RR database stores whole units, so convert uSv to Sv.
    SvPerHr = float(dData['uSvPerHr']) * 1.0E-06 

    # Format the rrdtool update command.
    strCmd = "rrdtool update %s %s:%s:%s" % \
                       (_RRD_FILE, dData['ELT'], dData['CPM'], SvPerHr)
    if verboseDebug:
        print "%s" % strCmd # DEBUG

    # Run the command as a subprocess.
    try:
        subprocess.check_output(strCmd, shell=True,  \
                             stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError, exError:
        print "%s: rrdtool update failed: %s" % \
                    (getTimeStamp(), exError.output)
        if exError.output.find("illegal attempt to update using time") > -1:
            remoteDeviceReset = True
            print "%s: rebooting radiation monitor" % (getTimeStamp())
        return False
    else:
        if debugOption:
            print 'database update sucessful'
        return True
##end def

def createGraph(fileName, dataItem, gLabel, gTitle, gStart,
                lower, upper, addTrend, autoScale):
    """Uses rrdtool to create a graph of specified weather data item.
       Parameters:
           fileName - name of file containing the graph
           dataItem - data item to be graphed
           gLabel - string containing a graph label for the data item
           gTitle - string containing a title for the graph
           gStart - beginning time of the graphed data
           lower - lower bound for graph ordinate #NOT USED
           upper - upper bound for graph ordinate #NOT USED
           addTrend - 0, show only graph data
                      1, show only a trend line
                      2, show a trend line and the graph data
           autoScale - if True, then use vertical axis auto scaling
               (lower and upper parameters are ignored), otherwise use
               lower and upper parameters to set vertical axis scale
       Returns: True if successful, False otherwise
    """
    gPath = _CHARTS_DIRECTORY + fileName + ".png"
    trendWindow = { 'end-1day': 7200,
                    'end-4weeks': 172800,
                    'end-12months': 604800 }
 
    # Format the rrdtool graph command.

    # Set chart start time, height, and width.
    strCmd = "rrdtool graph %s -a PNG -s %s -e now -w %s -h %s " \
             % (gPath, gStart, _CHART_WIDTH, _CHART_HEIGHT)
   
    # Set the range and scaling of the chart y-axis.
    if lower < upper:
        strCmd  +=  "-l %s -u %s -r " % (lower, upper)
    elif autoScale:
        strCmd += "-A "
    strCmd += "-Y "

    # Set the chart ordinate label and chart title. 
    strCmd += "-v %s -t %s " % (gLabel, gTitle)
 
    # Show the data, or a moving average trend line over
    # the data, or both.
    strCmd += "DEF:dSeries=%s:%s:LAST " % (_RRD_FILE, dataItem)
    if addTrend == 0:
        strCmd += "LINE1:dSeries#0400ff "
    elif addTrend == 1:
        strCmd += "CDEF:smoothed=dSeries,%s,TREND LINE3:smoothed#ff0000 " \
                  % trendWindow[gStart]
    elif addTrend == 2:
        strCmd += "LINE1:dSeries#0400ff "
        strCmd += "CDEF:smoothed=dSeries,%s,TREND LINE3:smoothed#ff0000 " \
                  % trendWindow[gStart]
     
    if verboseDebug:
        print "%s\n" % strCmd # DEBUG
    
    # Run the formatted rrdtool command as a subprocess.
    try:
        result = subprocess.check_output(strCmd, \
                     stderr=subprocess.STDOUT,   \
                     shell=True)
    except subprocess.CalledProcessError, exError:
        print "rrdtool graph failed: %s" % (exError.output)
        return False

    if debugOption:
        print "rrdtool graph: %s" % result
    return True

##end def

def generateGraphs():
    """Generate graphs for display in html documents.
       Parameters: none
       Returns: nothing
    """
    autoScale = False

    createGraph('24hr_cpm', 'CPM', 'counts\ per\ minute', 
                'CPM\ -\ Last\ 24\ Hours', 'end-1day', 0, 0, 2, autoScale)
    createGraph('24hr_svperhr', 'SvperHr', 'Sv\ per\ hour',
                'Sv/Hr\ -\ Last\ 24\ Hours', 'end-1day', 0, 0, 2, autoScale)
    createGraph('4wk_cpm', 'CPM', 'counts\ per\ minute',
                'CPM\ -\ Last\ 4\ Weeks', 'end-4weeks', 0, 0, 2, autoScale)
    createGraph('4wk_svperhr', 'SvperHr', 'Sv\ per\ hour',
                'Sv/Hr\ -\ Last\ 4\ Weeks', 'end-4weeks', 0, 0, 2, autoScale)
    createGraph('12m_cpm', 'CPM', 'counts\ per\ minute',
                'CPM\ -\ Past\ Year', 'end-12months', 0, 0, 2, autoScale)
    createGraph('12m_svperhr', 'SvperHr', 'Sv\ per\ hour',
                'Sv/Hr\ -\ Past\ Year', 'end-12months', 0, 0, 2, autoScale)
##end def

def getCLarguments():
    """Get command line arguments.  There are four possible arguments
          -d turns on debug mode
          -v turns on verbose debug mode
          -t sets the radiation device query interval
          -u sets the url of the radiation monitoring device
       Returns: nothing
    """
    global debugOption, verboseDebug, dataRequestInterval, \
           radiationMonitorUrl

    index = 1
    while index < len(sys.argv):
        if sys.argv[index] == '-d':
            debugOption = True
        elif sys.argv[index] == '-v':
            debugOption = True
            verboseDebug = True
        elif sys.argv[index] == '-t':
            try:
                dataRequestInterval = abs(int(sys.argv[index + 1]))
            except:
                print "invalid polling period"
                exit(-1)
            index += 1
        elif sys.argv[index] == '-u':
            radiationMonitorUrl = sys.argv[index + 1]
            index += 1
        else:
            cmd_name = sys.argv[0].split('/')
            print "Usage: %s [-d] [-t seconds] [-u url}" % cmd_name[-1]
            exit(-1)
        index += 1
##end def

def main():
    """Handles timing of events and acts as executive routine managing
       all other functions.
       Parameters: none
       Returns: nothing
    """
    signal.signal(signal.SIGTERM, terminateAgentProcess)

    print '%s starting up radmon agent process' % \
                  (getTimeStamp())

    # last time output JSON file updated
    lastDataRequestTime = -1
    # last time charts generated
    lastChartUpdateTime = - 1
    # last time the rrdtool database updated
    lastDatabaseUpdateTime = -1

    ## Get command line arguments.
    getCLarguments()

    ## Exit with error if rrdtool database does not exist.
    if not os.path.exists(_RRD_FILE):
        print 'rrdtool database does not exist\n' \
              'use createWeatherRrd script to ' \
              'create rrdtool database\n'
        exit(1)
 
    ## main loop
    while True:

        currentTime = time.time() # get current time in seconds

        # Every web update interval request data from the radiation
        # monitor and process the received data.
        if currentTime - lastDataRequestTime > dataRequestInterval:
            lastDataRequestTime = currentTime
            dData = {}
            result = True

            # Get the data string from the device.
            sData = getRadiationData()
            if sData == None:
                result = False

            # If successful parse the data.
            if result:
                result = parseDataString(sData, dData)

            # If parsing successful, convert the data.
            if result:
                result = convertData(dData)

            # If conversion successful, write data to data files.
            if result:
                writeInputDataFile(sData)
                writeOutputDataFile(dData)

                # At the rrdtool database update interval, update the database.
                if currentTime - lastDatabaseUpdateTime > \
                        _DATABASE_UPDATE_INTERVAL:   
                    lastDatabaseUpdateTime = currentTime
                    ## Update the round robin database with the parsed data.
                    updateDatabase(dData)

            # Set the station status to online or offline depending on the
            # success or failure of the above operations.
            setStationStatus(result)


        # At the chart generation interval, generate charts.
        if currentTime - lastChartUpdateTime > _CHART_UPDATE_INTERVAL:
            lastChartUpdateTime = currentTime
            p = multiprocessing.Process(target=generateGraphs, args=())
            p.start()

        # Relinquish processing back to the operating system until
        # the next update interval.

        elapsedTime = time.time() - currentTime
        if debugOption and not verboseDebug:
            print
        if verboseDebug:
            print "processing time: %6f sec\n" % elapsedTime
        remainingTime = dataRequestInterval - elapsedTime
        if remainingTime > 0.0:
            time.sleep(remainingTime)
    ## end while
    return
## end def

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print '\n',
        terminateAgentProcess('KeyboardInterrupt','Module')
