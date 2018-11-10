#!/usr/bin/python3

import ctypes
import os
import configparser
import signal
import sys
import time
import logging
from types import SimpleNamespace
from threading import Event, Timer

import pygame
import i3ipc
from PIL import Image
from xdg.BaseDirectory import xdg_config_home

from exthread import ExThread
from config import CONF

BLACKLIST = ['i3expod.py', None]

LOG = logging.getLogger('updt')

def lockable(f):
    def wrapper(*args, **kwargs):
        if not wrapper.locked:
            wrapper.locked = True
            ret = f(*args, **kwargs)
            wrapper.locked = False
            return ret
        else:
            logging.debug('Function %s locked, canceling', f.__name__)
    wrapper.locked = False
    return wrapper


class Updater(ExThread):
    def __init__(self, workspaces_instance):
        ExThread.__init__(self)
        self.daemon = True

        self.con = i3ipc.Connection()
        self.i3_thread = None

        self.workspaces = workspaces_instance
        for workspace in self.con.get_tree().workspaces():
            self.workspaces.init_workspace(workspace.num)

        self._running = Event()
        self._running.set()
        self._stop = Event()

        self.start_timer()

        self.con.on('window::move', self.update)
        self.con.on('window::floating', self.update)
        self.con.on('window::fullscreen_mode', self.update)
        self.con.on('window::focus', self.update)

        self.con.on('workspace::init', self.initialize_workspace)
        self.con.on('workspace::empty', self.remove_workspace)
        self.con.on('workspace::rename', self.rename_workspace)

    def run(self):
        self.update()

        self.i3_thread = ExThread(target=self.con.main)
        self.i3_thread.daemon = True
        self.i3_thread.start()
        LOG.info('i3ipc thread running')

        self._stop.wait()

    def reset(self):
        LOG.warning('Reinitializing updater')
        self.workspaces.reset()
        self.update()

    def destroy(self):
        LOG.warning('Shutting down updater')
        self.con.main_quit()
        self._stop.set()

    def stop_timer(self, quiet=False):
        if not quiet:
            LOG.info('Stopping forced background updates')
        self.timer.cancel()

    def start_timer(self, quiet=False):
        if not quiet:
            LOG.info('Starting forced background updates')
        self.timer = Timer(CONF.forced_update_interval_sec, self.update)
        self.timer.start()

    def set_new_update_timer(self):
        LOG.info('Resetting update timer')
        self.stop_timer(quiet=True)
        self.start_timer(quiet=True)

    def initialize_workspace(self, ipc, stack_frame):
        del ipc
        if not stack_frame.current.name == 'i3expo-temporary-workspace':
            LOG.info('New workspace: %s', stack_frame.current.num)
            self.workspaces.init_workspace(stack_frame.current.num)

    def remove_workspace(self, ipc, stack_frame):
        del ipc
        if not stack_frame.current.name == 'i3expo-temporary-workspace':
            LOG.info('Workspace deleted: %s', stack_frame.current.num)
            self.workspaces.remove_workspace(stack_frame.current.num)

    def rename_workspace(self, ipc, stack_frame):
        del ipc
        if not stack_frame.current.name == 'i3expo-temporary-workspace':
            LOG.info('Workspace renamed: %s', stack_frame.current.num)
            self.workspaces.rename_workspace(stack_frame.current.num)

    @lockable
    def update(self, ipc=None, stack_frame=None):
        if stack_frame is None:
            LOG.debug('Update check triggered manually')
        else:
            if stack_frame.container.window_class in BLACKLIST:
                LOG.debug('Update check from %s discarded',
                               stack_frame.container.window_class)
                return False
            LOG.debug('Update check triggered by %s: %s',
                           stack_frame.change, stack_frame.container.window_class)
        del ipc

        self._running.wait()

        for index in self.workspaces:
            self.workspaces[index].update()

        self.set_new_update_timer()
        return True

    def lock(self):
        self._running.clear()
        LOG.info('Pausing updater')

    def unlock(self):
        self._running.set()
        self.set_new_update_timer()
        LOG.info('Starting updater')
