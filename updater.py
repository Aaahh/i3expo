#!/usr/bin/python3

import ctypes
import os
import configparser
import signal
import sys
import time
import logging
from types import SimpleNamespace
from threading import Thread, Event, Timer

import pygame
import i3ipc
from PIL import Image
from xdg.BaseDirectory import xdg_config_home

from config import read_config

SCREENSHOT_LIB = 'prtscn.so'
SCREENSHOT_LIB_PATH = os.path.dirname(os.path.abspath(__file__)) + os.path.sep + SCREENSHOT_LIB
GRAB = ctypes.CDLL(SCREENSHOT_LIB_PATH)
GRAB.getScreen.argtypes = []
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


class Updater(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.daemon = True

        self.active_workspace = None

        self.con = i3ipc.Connection()
        self.read_config()
        self.init_knowledge()

        self._running = Event()
        self._running.set()
        self._stop = Event()

        self.start_timer()

        self.con.on('window::move', self.update)
        self.con.on('window::floating', self.update)
        self.con.on('window::fullscreen_mode', self.update)
        self.con.on('window::focus', self.update)

    def run(self):
        self.update()

        i3_thread = Thread(target=self.con.main)
        i3_thread.daemon = True
        i3_thread.start()
        LOG.info('i3ipc thread running')

        self._stop.wait()

    def reset(self):
        LOG.warning('Reinitializing updater')
        self.read_config()
        self.init_knowledge()
        self.update()

    def destroy(self):
        LOG.warning('Shutting down updater')
        self.con.main_quit()
        self._stop.set()

    def init_workspace(self, workspace):
        self.knowledge[workspace.num] = {
            'name':         workspace.name,
            'screenshot':   None,
            'last-update':  0.0,
            'state':        ()
        }
        for cont in workspace.leaves():
            self.knowledge[workspace.num]['state'] += \
                    ((cont.id, cont.rect.x, cont.rect.y, cont.rect.width, cont.rect.height),)

    def init_knowledge(self):
        LOG.info('Initializing workspace knowledge')
        self.knowledge = {}
        for workspace in self.con.get_tree().workspaces():
            if workspace.num not in self.knowledge.keys():
                self.init_workspace(workspace)

        self.active_workspace = self.con.get_tree().find_focused().workspace().num

    def stop_timer(self, quiet=False):
        if not quiet:
            LOG.info('Stopping forced background updates')
        try:
            self.timer.cancel()
        except AttributeError:
            pass

    def start_timer(self, quiet=False):
        if not quiet:
            LOG.info('Starting forced background updates')
        self.timer = Timer(self.conf.forced_update_interval_sec, self.update)
        self.timer.start()

    def set_new_update_timer(self):
        LOG.info('Resetting update timer')
        self.stop_timer(quiet=True)
        self.start_timer(quiet=True)

    def data_older_than(self, what):
        old = self.knowledge[self.active_workspace]['screenshot'] is None or \
                time.time() - self.knowledge[self.active_workspace]['last-update'] > what
        LOG.debug('Screenshot data for workspace %s is%solder than %ss',
                       self.active_workspace, (' not ' if not old else ' '), what)
        return old

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

        if not self.data_older_than(self.conf.min_update_interval_sec):
            return False

        tree = self.con.get_tree()
        workspace = tree.find_focused().workspace()
        self.active_workspace = workspace.num

        if self.active_workspace not in self.knowledge.keys():
            self.init_workspace(workspace)

        wspace_nums = [w.num for w in tree.workspaces()]
        deleted = []
        for item in self.knowledge:
            if item not in wspace_nums:
                deleted.append(item)
        for item in deleted:
            del self.knowledge[item]

        if self.active_workspace_state_has_changed() or \
           self.data_older_than(self.conf.forced_update_interval_sec):
            LOG.debug('Fetching update data for workspace %s', self.active_workspace)
            screenshot = self.grab_screen()
            if self._running.is_set():
                self.knowledge[self.active_workspace]['screenshot'] = screenshot
                self.knowledge[self.active_workspace]['last-update'] = time.time()

        self.set_new_update_timer()
        return True

    def active_workspace_state_has_changed(self):
        state = ()
        for cont in self.con.get_tree().find_focused().workspace().leaves():
            state += ((cont.id, cont.rect.x, cont.rect.y, cont.rect.width, cont.rect.height),)

        if self.knowledge[self.active_workspace]['state'] == state:
            LOG.debug('Workspace %s has not changed', self.active_workspace)
            return False

        self.knowledge[self.active_workspace]['state'] = state
        LOG.debug('Workspace %s has changed', self.active_workspace)
        return True

    def read_config(self):
        LOG.warning('Reading config file')
        self.conf = read_config()

    def grab_screen(self):
        LOG.debug('Taking a screenshot, probably of workspace %s', self.active_workspace)

        width = self.conf.screenshot_width - self.conf.screenshot_offset_x
        height = self.conf.screenshot_height - self.conf.screenshot_offset_y
        size = width * height
        objlength = size * 3

        result = (ctypes.c_ubyte * objlength)()

        GRAB.getScreen(self.conf.screenshot_offset_x, self.conf.screenshot_offset_y,
                       width, height, result)
        LOG.debug('Screenshot taken, probably of workspace %s', self.active_workspace)
        return (width, height, result)

    def lock(self):
        self._running.clear()
        LOG.info('Pausing updater')

    def unlock(self):
        self._running.set()
        self.set_new_update_timer()
        LOG.info('Starting updater')
