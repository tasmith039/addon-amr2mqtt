#!/usr/bin/env python3
"""
Runs rtlamr to watch for IDM broadcasts from power meter. If meter id
is in the list, usage is sent to 'readings/{meter id}/meter_reading'
topic on the MQTT broker specified in settings.

WATCHED_METERS = A Python list indicating those meter IDs to record and post.
MQTT_HOST = String containing the MQTT server address.
MQTT_PORT = An int containing the port the MQTT server is active on.

"""
import logging
import subprocess
import signal
import sys
import time
import paho.mqtt.publish as publish
import settings


def shutdown():
    """Uses signal to shutdown and hard kill opened processes and self."""
    rtltcp.send_signal(15)
    rtlamr.send_signal(15)
    time.sleep(1)
    rtltcp.send_signal(9)
    rtlamr.send_signal(9)
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

# stores last interval id to avoid duplication, includes getter and setter
last_reading = {}

if len(settings.MQTT_USER) and len(settings.MQTT_PASSWORD):
    AUTH = {"username": settings.MQTT_USER, "password": settings.MQTT_PASSWORD}
else:
    AUTH = None

logging.basicConfig()
logging.getLogger().setLevel(settings.LOG_LEVEL)


def get_last_interval(meter_id):  # pylint: disable=redefined-outer-name
    """Get last interval."""
    return last_reading.get(meter_id, (None))


def set_last_interval(meter_id, interval_id):  # pylint: disable=redefined-outer-name
    """Set last interval."""
    last_reading[meter_id] = interval_id


def send_mqtt(
    topic,
    payload,
):
    """Send data to MQTT broker defined in settings."""
    try:
        publish.single(
            topic,
            payload=payload,
            qos=1,
            hostname=settings.MQTT_HOST,
            port=settings.MQTT_PORT,
            auth=AUTH,
            tls=settings.MQTT_TLS,
            client_id=settings.MQTT_CLIENT_ID,
        )
    except Exception as ex:  # pylint: disable=broad-except
        logging.error("MQTT Publish Failed: %s", str(ex))


# start the rtl_tcp program
rtltcp = subprocess.Popen(
    [settings.RTL_TCP + " > /dev/null 2>&1 &"],
    shell=True,
    stdin=None,
    stdout=None,
    stderr=None,
    close_fds=True,
)
time.sleep(5)

# start the rtlamr program.
rtlamr_cmd = [settings.RTLAMR, "-msgtype=idm", "-format=csv"]
rtlamr = subprocess.Popen(
    rtlamr_cmd,
    stdout=subprocess.PIPE,
    universal_newlines=True,
)

while True:
    try:
        amrline = rtlamr.stdout.readline().strip()
        flds = amrline.split(",")

        if len(flds) != 66:
            # proper IDM results have 66 fields
            continue

        # make sure the meter id is one we want
        meter_id = int(flds[9])
        if settings.WATCHED_METERS and meter_id not in settings.WATCHED_METERS:
            continue

        # get some required info: current meter reading,
        # current interval id, most recent interval usage
        read_cur = int(flds[15])
        interval_cur = int(flds[10])
        idm_read_cur = int(flds[16])

        # retreive the interval id of the last time we sent to MQTT
        interval_last = get_last_interval(meter_id)

        if interval_cur != interval_last:

            # as observed on on my meter...
            # using values set in settings...
            # each idm interval is 5 minutes (12x per hour),
            # measured in hundredths of a kilowatt hour
            # take the last interval usage times 10 to get watt-hours,
            # then times 12 to get average usage in watts
            rate = idm_read_cur * settings.WH_MULTIPLIER * settings.READINGS_PER_HOUR

            current_reading_in_kwh = (read_cur * settings.WH_MULTIPLIER) / 1000

            logging.debug(
                "Sending meter %s reading: %s", meter_id, current_reading_in_kwh
            )
            send_mqtt(
                f"${settings.MQTT_BASE_TOPIC}/${meter_id}/meter_reading",
                str(current_reading_in_kwh),
            )

            logging.debug("Sending meter %s rate: %s", meter_id, rate)
            send_mqtt(f"${settings.MQTT_BASE_TOPIC}/${meter_id}/meter_rate", str(rate))

            # store interval ID to avoid duplicating data
            set_last_interval(meter_id, interval_cur)

    except Exception as ex:  # pylint: disable=broad-except
        logging.debug("Exception squashed! %s: %s", ex.__class__.__name__, ex)
        time.sleep(2)