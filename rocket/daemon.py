# Copyright 2009 Kaspars Dancis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import logging
import os
import sys
import time
from sys import stdout
from threading import Thread

from common import *

if __name__ == "__main__":
    import_config = "from config import *"

    daemon = False
    in_loop = False
    profile = False

    for arg in sys.argv[1:]:
        if arg == "-b":
            daemon = True
        elif arg == "-l":
            in_loop = True
        elif arg == "-p":
            profile = True
        elif arg.startswith("-c="):
            if arg.endswith(".py"):
                config = arg[3:len(arg) - 3]
            else:
                config = arg[3:]

            import_config = "from %s import *" % config
        else:
            print "Usage: daemon.py [-b|-l] [-c=config file]"
            print '-b: run in background loop mode (*nix platforms only)'
            print '-l: run in loop mode'
            print '-c: configuration file, by default "config"'
            sys.exit()

    exec import_config

DEFAULT_IDLE_TIME = 60


class Service(Thread):
    def __init__(self, name, config):
        Thread.__init__(self)
        self.name = name
        self.config = config

        if IDLE_TIME in self.config:
            self.idle_time = self.config[IDLE_TIME]
        else:
            self.idle_time = DEFAULT_IDLE_TIME

        self.single_run = False

    def run(self):
        self.on_start()

        try:
            if self.single_run:
                try:
                    self.process()
                except:
                    logging.exception("%s Error:", self.name)
            else:
                while True:
                    try:
                        while self.process():
                            pass
                    except:
                        logging.exception("%s Error:", self.name)

                    logging.info('%s Idling for %d seconds',
                                 self.name, self.idle_time)
                    time.sleep(self.idle_time)
        finally:
            self.on_stop()

    def on_start(self):
        pass

    def on_stop(self):
        pass

    def process(self):
        logging.fatal('%s Process method is not implemented', self.name)
        return False


services = []


def run_services(in_loop):
    for service_name in SERVICES.keys():
        config = SERVICES[service_name]
        type = config[TYPE]  # TODO: add error handling here
        i = type.rfind('.')
        service_package = type[:i]
        service_class = type[i + 1:]
        exec "from %s import %s" % (service_package, service_class)
        exec ("service = %s(service_name, SERVICES[service_name])"
              % service_class)

        if not in_loop:
            service.single_run = True

        services.append(service)

        service.start()


def main():
    sys.stdout = sys.stderr = o = open(DAEMON_LOG, 'a+')
    logging.basicConfig(level=LOG_LEVEL,
                        format='%(asctime)s %(levelname)s %(message)s',
                        stream=o)

    os.setegid(GID)
    os.seteuid(UID)

    run_services(True)


def start_profiling():
    import cherrypy
    import dowser

    cherrypy.tree.mount(dowser.Root())

    cherrypy.config.update({
        'environment': 'embedded',
        'server.socket_host': '0.0.0.0',
        'server.socket_port': 8484,
    })

    cherrypy.engine.start()


if __name__ == "__main__":
    if daemon:
        try:
            pid = os.fork()
            if pid > 0:
                # exit first parent
                sys.exit(0)
        except OSError, e:
            logging.error("Daemon Error: Fork #1 failed: %d (%s)",
                          e.errno, e.strerror)
            sys.exit(1)

        # decouple from parent environment
        os.chdir("/")   # don't prevent unmounting....
        os.setsid()
        os.umask(0)

        # do second fork
        try:
            pid = os.fork()
            if pid > 0:
                logging.info("Daemon PID %d" % pid)
                open(DAEMON_PID, 'w').write("%d" % pid)
                sys.exit(0)
        except OSError, e:
            logging.error("Daemon Error: Fork #2 failed: %d (%s)",
                          e.errno, e.strerror)
            sys.exit(1)

        if profile:
            start_profiling()

        # start the daemon main loop
        main()
    else:
        logging.basicConfig(level=LOG_LEVEL,
                            format='%(asctime)s %(levelname)s %(message)s',
                            stream=stdout)

        if profile:
            start_profiling()

        run_services(in_loop)
