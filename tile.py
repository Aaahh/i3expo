#!/usr/bin/python3

import time
import logging

import pygame
import i3ipc
from PIL import Image

from config import CONF

LOG = logging.getLogger('tile')

pygame.font.init()
QUESTION_MARK = pygame.font.SysFont('sans-serif', 250).render('?', True, (150, 150, 150))


def process_img(raw_img):
    #try:
    #except TypeError: #TODO: Remove?
    #    return None

    pil = Image.frombuffer('RGB', (raw_img[0], raw_img[1]), raw_img[2], 'raw', 'RGB', 0, 1)
    return pygame.image.fromstring(pil.tobytes(), pil.size, pil.mode)


def fit_image(image):
    if CONF.thumb_stretch:
        image = pygame.transform.smoothscale(image, CONF.tile_dim_inner)
        offset = [0, 0]
    else:
        image_size = image.get_rect().size
        ratio = [CONF.tile_dim_inner[n] / image_size[n] for n in [0, 1]]
        if ratio[0] < ratio[1]:
            result = [CONF.tile_dim_inner[0], round(ratio[0] * image_size[1])]
            offset = [0, round((CONF.tile_dim_inner[1] - result[1]) / 2)]
        else:
            result = [round(ratio[1] * image_size[0]), CONF.tile_dim_inner[1]]
            offset = [round((CONF.tile_dim_inner[0] - result[0]) / 2), 0]
        image = pygame.transform.smoothscale(image, result)
    return offset, image


class Lightmask:
    lightmask = None

    @classmethod
    def set_mask(self):
        self.lightmask = pygame.Surface(CONF.tile_dim_outer, pygame.SRCALPHA, 32)
        self.lightmask.fill((255, 255, 255, 255 * CONF.highlight_percentage / 100))

    def __init__(self):
        if self.lightmask is None:
            self.__class__.set_mask()

    def get_masked(self, unmasked):
        masked = unmasked.copy()
        masked.blit(self.lightmask, (0, 0))
        return masked

class BaseTile:
    def __init__(self, parent):
        self.workspace = parent
        self.screenshot = None
        self.surface = {
                'mouseon': None,
                'mouseoff': None
        }
        self.rect = None
        self.color = {
                'frame': None,
                'tile': None
        }
        self.last = {
                'screenshot': None,
                'state': None,
                'any': None
        }
        self.label = None

        self.initialize()

    def initialize(self):
        raise NotImplementedError("BaseTile cannot be instantiated!")

    def set_rect(self):
        idx_x = (self.workspace.index - 1) % CONF.grid[0]
        idx_y = (self.workspace.index - 1) // CONF.grid[0]
        origin_x = CONF.padding[0] + (CONF.tile_dim_outer[0] + CONF.spacing[0]) * idx_x
        origin_y = CONF.padding[1] + (CONF.tile_dim_outer[1] + CONF.spacing[1]) * idx_y
        origin = [origin_x, origin_y]
        self.rect = pygame.Rect(origin + CONF.tile_dim_outer)

    def set_colors(self):
        pass

    def set_surface(self):
        tile = pygame.Surface(CONF.tile_dim_outer)
        tile.fill(self.color['frame'])
        tile.fill(self.color['tile'], [CONF.frame_width_px] * 2 + CONF.tile_dim_inner)
        if self.screenshot:
            offset, image = fit_image(self.screenshot)
            tile.blit(image, [offset[n] + CONF.frame_width_px for n in (0,1)])
        self.surface['mouseoff'] = tile
        self.surface['mouseon'] = Lightmask().get_masked(tile)
        LOG.debug('Surface updated for tile %s', self.workspace.index)

    def update(self):
        return False


class CapturedTile(BaseTile):
    def initialize(self):
        self.set_rect()
        self.set_colors()
        self.set_screenshot()
        self.set_surface()
        self.label = Label(self)

    def set_last(self, what):
        when = time.time()
        self.last[what] = when
        self.last['any'] = when

    def set_colors(self):
        old_color = self.color
        if self.workspace.state:
            if self.workspace.workspaces.active_workspace == self.workspace.index:
                self.color = CONF.colors['active']
            else:
                self.color = CONF.colors['inactive']

        if old_color != self.color:
            self.set_last('state')
            LOG.debug('Colors updated for tile %s', self.workspace.index)
            return True
        else:
            return False

    def set_screenshot(self):
        if self.workspace.last['screenshot'] is not None and \
                (self.last['screenshot'] is None or
                 self.last['screenshot'] < self.workspace.last['screenshot']):
            self.screenshot = process_img(self.workspace.screenshot)
            self.set_last('screenshot')
            LOG.debug('Screenshot updated for tile %s', self.workspace.index)
            return True
        return False

    def update(self):
        colors_changed = self.set_colors()
        screenshot_changed = self.set_screenshot()

        LOG.debug('Colors %schanged, screenshot %schanged',
                  '' if colors_changed else 'un', '' if screenshot_changed else 'un')

        if colors_changed or screenshot_changed:
            self.set_surface()


class UnknownTile(BaseTile):
    def initialize(self):
        self.set_rect()
        self.color = CONF.colors['unknown']
        self.screenshot = QUESTION_MARK
        self.set_surface()
        self.label = Label(self)


class EmptyTile(BaseTile):
    def initialize(self):
        self.set_rect()
        self.color = CONF.colors['empty']
        self.set_surface()


class Label:
    def __init__(self, parent):
        self.tile = parent
        self.rect = ()
        self.surface = None

        self.set_surface()

    def set_surface(self):
        font = pygame.font.SysFont(CONF.names_font, CONF.names_fontsize)
        defined_name = False
        try:
            defined_name = CONF.workspace_names[self.tile.workspace.index]
        except KeyError:
            pass

        if self.tile.workspace.state or defined_name:
            if not defined_name:
                name = self.tile.workspace.title
            else:
                name = defined_name

            name = font.render(name, True, CONF.names_color)
            name_rect = name.get_rect().size
            self.rect = pygame.Rect(self.tile.rect.topleft[0] + round(CONF.tile_dim_outer[0] - name_rect[0]) / 2,
                                    self.tile.rect.topleft[1] + round(CONF.tile_dim_outer[1] * 1.02),
                                    name_rect[0],
                                    name_rect[1])

            self.surface = name
