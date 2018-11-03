#!/usr/bin/python3

import time
import logging

import pygame
import i3ipc
from PIL import Image

LOG = logging.getLogger('stat')


def process_img(raw_img):
    try:
        pil = Image.frombuffer('RGB', (raw_img[0], raw_img[1]), raw_img[2], 'raw', 'RGB', 0, 1)
    except TypeError:
        return None
    return pygame.image.fromstring(pil.tobytes(), pil.size, pil.mode)


class Workspaces:
    def __init__(self):
        self.workspaces = {}
        self.active_workspace = None

    def initialize(self, index):
        self.workspaces[index] = Workspace(index)

    def remove(self, index):
        del self.workspaces[index]

    def clear(self,index):
        self.remove(index)
        self.initialize(index)


class Workspace:
    def __init__(self, num):
        self.index = num
        self.title = name
        self.active = False
        self.last_update = None
        self.state = ()
        self.screenshot = None
        self.tile = Tile(self)


class Tile:
    def __init__(self, parent):
        self.workspace = parent
        self.active = False
        self.screenshot = {
                'mouseon': None,
                'mouseoff': None
        }
        self.dim_outer = ()
        self.dim_inner = ()


