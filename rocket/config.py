import os
import sys

sys.path.insert(0, os.path.abspath(".."))

from common import *

# See https://github.com/k7d/approcket/wiki/Configuration-Reference for
# full list of supported options.

# Replication URL - change this to URL corresponding your application
ROCKET_URL = "http://localhost:8080/rocket"

SERVICES = {
# Define replication services for entities that you want to be replicated here.

# Example:
    "ReplicateNotAComment": {TYPE: REPLICATION_SERVICE,
                             KIND: "NotAComment"},
    "ReplicateComment": {TYPE: REPLICATION_SERVICE,
                         KIND: "Comment",
                         EMBEDDED_LIST_FIELDS: ["list2"]},
}

# number of AppEngine entities to load in a single request, reduce this number
# if requests are using too much CPU cycles or are timing out
BATCH_SIZE = 150

# How many times AppRocket will retry sending an update to AppEngine in case
# of server error (such as AppEngine timeout).
# This setting does not impact consistency, since if all retries fail,
# AppRocket will exit current cycle, sleep for IDLE_TIME and try again
# infinitely.
SEND_RETRY_COUNT = 3


# MYSQL DATABASE CONFIGURATION
DATABASE_HOST = "localhost"
DATABASE_NAME = "approcket"
DATABASE_USER = "approcket"
DATABASE_PASSWORD = "approcket"
DATABASE_PORT = 3306
DATABASE_ENGINE = "InnoDB"

#LOGGING CONFIGURATION
import logging
LOG_LEVEL = logging.INFO

# DAEMON CONFIGURATION
# This provides configuration for running AppRocket replicator (station.py)
# in daemon mode (using -d command-line switch).
LOGFILE = '/var/log/approcket.log'
PIDFILE = '/var/run/approcket.pid'
GID = 103
UID = 103

# REQUEST TIMEOUT
import socket
socket.setdefaulttimeout(30)
