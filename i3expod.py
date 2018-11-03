#!/usr/bin/python3

import signal
import sys
import time
import logging

from updater import Updater
from interface import Interface

logging.basicConfig(format='%(asctime)s.%(msecs)03d - %(name)4s %(levelname)-8s: %(message)s',
                    datefmt='%T', level=logging.DEBUG)


def sig_hup(event, stack_frame):
    del event
    del stack_frame
    logging.info('SIGHUP received')
    UPDATER.reset()
    INTERFACE.reset()


def sig_usr1(event, stack_frame):
    del event
    del stack_frame
    logging.info('SIGUSR1 received')
    INTERFACE.toggle()


def sig_int(event, stack_frame):
    del event
    del stack_frame
    logging.info('SIGINT received')
    UPDATER.destroy()
    INTERFACE.destroy()
    logging.warning('Shutting down')
    sys.exit(0)


if __name__ == '__main__':
    signal.signal(signal.SIGHUP, sig_hup)
    signal.signal(signal.SIGUSR1, sig_usr1)
    signal.signal(signal.SIGINT, sig_int)

    logging.info('Setting up updater')
    UPDATER = Updater()
    UPDATER.start()
    logging.info('Initializing interface')
    INTERFACE = Interface(UPDATER)
    INTERFACE.start()
    logging.warning('Setup finished')

    while True:
        time.sleep(10)
