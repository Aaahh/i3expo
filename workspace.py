#!/usr/bin/python3

import time
import logging
import os
import ctypes

import pygame
import i3ipc
from PIL import Image

from config import CONF
from tile import CapturedTile, UnknownTile, EmptyTile

LOG = logging.getLogger('work')
SCREENSHOT_LIB = 'prtscn.so'
SCREENSHOT_LIB_PATH = os.path.dirname(os.path.abspath(__file__)) + os.path.sep + SCREENSHOT_LIB
GRAB = ctypes.CDLL(SCREENSHOT_LIB_PATH)
GRAB.getScreen.argtypes = []


class Workspaces(dict):
    def __init__(self):
        self.active_workspace = None

    def __getitem__(self, key):
        if not isinstance(key, int) or key < 0:
            raise KeyError('Invalid key: {}'.format(key))
        if key in self:
            return dict.__getitem__(self, key)
        else:
            LOG.debug('Requested workspace %s empty, creating dummy', key)
            self[key] = DummyWorkspace(key)
            return dict.__getitem__(self, key)

    def init_workspace(self, index):
        self[index] = Workspace(self, index)

    def remove_workspace(self, index):
        LOG.info('Deleting workspace %s', index)
        del self[index]

    def clear(self,index):
        LOG.info('Resetting workspace %s', index)
        self.remove_workspace(index)
        self.init_workspace(index)

    def rename_workspace(self, index):
        LOG.info('Renaming workspace %s', index)
        self[index].set_name()

    def set_active(self, index):
        LOG.debug('Active workspace is now %s', index)
        self.active_workspace = index

    def reset(self):
        LOG.warning('Resetting all workspace data')
        self.__init__()

class DummyWorkspace:
    def __init__(self, num):
        self.index = num
        self.name = None
        self.last = {
                'state': None,
                'screenshot': None,
                'any': None
        }
        self.state = ()
        self.tile = EmptyTile(self)

    def update(self):
        pass

class Workspace:
    def __init__(self, parent, num):
        self.con = i3ipc.Connection()

        self.workspaces = parent

        self.index = num
        self.name = None
        self.last = {
                'state': None,
                'screenshot': None,
                'any': None
        }
        self.state = ()
        self.screenshot = None
        self.tile = None

        self.set_name()
        self.tile = UnknownTile(self)

        LOG.info('Workspace %s (%s) initialized', self.index, self.name)

    def set_name(self):
        if self.index in CONF.workspace_names.keys():
            self.name = CONF.workspace_names[self.index]
        else:
            self.name = "abcd" # TODO

    def set_last(self, what):
        when = time.time()
        self.last[what] = when
        self.last['any'] = when

    def capture(self):
        LOG.debug('Taking screenshot of workspace %s: %s', self.index, self.name)
        width = CONF.screenshot_dim[0] - CONF.screenshot_offset[0]
        height = CONF.screenshot_dim[1] - CONF.screenshot_offset[1]
        size = width * height
        objlength = size * 3

        result = (ctypes.c_ubyte * objlength)()

        GRAB.getScreen(CONF.screenshot_offset_x, CONF.screenshot_offset_y,
                       width, height, result)
        if self.workspaces.active_workspace == self.index:
            self.screenshot = (width, height, result)
            self.set_last('screenshot')
            LOG.debug('Screenshot of workspace %s (%s) taken', self.index, self.name)
            return True
        LOG.debug('Screenshot of workspace %s (%s) discarded', self.index, self.name)
        return False

    def screenshot_older_than(self, what):
        old = self.screenshot is None or time.time() - self.last['screenshot'] > what
        LOG.debug('Screenshot data for workspace %s (%s) is%solder than %ss',
                  self.name, self.index, (' not ' if not old else ' '), what)
        return old

    def set_state(self):
        state = ()
        for workspace in self.con.get_tree().workspaces():
            if workspace.num == self.index:
                for cont in workspace.leaves():
                    state += ((cont.id, cont.rect.x, cont.rect.y, cont.rect.width, cont.rect.height),)
                break
        changed = self.state != state
        self.state = state
        LOG.debug('Workspace %s (%s) has%schanged',
                  self.index, self.name, (' ' if changed else ' not '))
        if changed:
            self.set_last('state')
        return changed

    def update(self):
        if self.last['state'] is not None and \
                time.time() - self.last['state'] < CONF.min_update_interval_sec:
            LOG.debug('Update for workspace %s (%s) discarded - bounce',
                      self.index, self.name)
            return False

        state_changed = self.set_state()

        if self.workspaces.active_workspace == self.index and \
                (state_changed or self.screenshot_older_than(CONF.forced_update_interval_sec)):
            if isinstance(self.tile, UnknownTile) and self.capture():
                LOG.info('Workspace %s (%s) captured for the first time',
                         self.index, self.name)
                self.tile = CapturedTile(self)
            elif isinstance(self.tile, CapturedTile):
                self.capture()

        return True
