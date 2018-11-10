#!/usr/bin/python3

import time
import logging
from threading import Thread, Event

import pygame
import i3ipc
from PIL import Image

from exthread import ExThread
from config import CONF

LOG = logging.getLogger('intf')


def process_img(raw_img):
    try:
        pil = Image.frombuffer('RGB', (raw_img[0], raw_img[1]), raw_img[2], 'raw', 'RGB', 0, 1)
    except TypeError:
        return None
    return pygame.image.fromstring(pil.tobytes(), pil.size, pil.mode)


class Interface(ExThread):
    def __init__(self, workspaces_instance, updater_instance):
        ExThread.__init__(self)
        self.daemon = True

        self.con = i3ipc.Connection()
        self.updater = updater_instance
        self.workspaces = workspaces_instance

        self._running = Event()
        self._stop = Event()

        self.last_shown = None
        self.drawn = {}

        self.screen_image = pygame.Surface(CONF.window_dim)
        self.screen = None

        self.prepare_ui()

    def run(self):
        self._stop.wait()

    def show_ui(self):
        self.updater.lock()

        self.active_tile = None

        self.con.command('workspace i3expo-temporary-workspace')

        self.blit_changes()

        LOG.info('UI updated')

        self.screen = pygame.display.set_mode(CONF.window_dim, pygame.RESIZABLE)
        pygame.display.set_caption('i3expo')
        self.screen.blit(self.screen_image.convert(), (0, 0))
        pygame.display.flip()
        self.last_shown = time.time()
        LOG.info('UI displayed')

        self.process_input()

        self._running.clear()
        LOG.debug('Pygame input processed')

        pygame.display.quit()
        time.sleep(0.1)  # TODO: Get rid of this - how?
        LOG.debug('Pygame window closed')
        self.updater.unlock()

    def prepare_ui(self):
        LOG.warning('Preparing UI')
        pygame.display.init()
        self.screen_image.fill(CONF.bgcolor)

    def reset(self):
        LOG.warning('Resetting UI instance')
        pygame.display.quit()
        self.__init__(self.updater)

    def destroy(self):
        LOG.warning('Shutting down UI')
        self._stop.set()

    def toggle(self):
        if self._running.is_set():
            LOG.info('Hiding UI')
            self._running.clear()
        else:
            LOG.info('Showing UI')
            self._running.set()
            self.show_ui()

    def blit_tile(self, index, active=False, target=None):
        target = target if target is not None else self.screen_image
        if active:
            LOG.debug('Blitting tile %s as active', index)
            target.blit(self.workspaces[index].tile.surface['mouseon'],
                        self.workspaces[index].tile.rect)
        else:
            LOG.debug('Blitting tile %s', index)
            target.blit(self.workspaces[index].tile.surface['mouseoff'],
                        self.workspaces[index].tile.rect)

    def blit_name(self, index):
        if CONF.names_show and self.workspaces[index].tile.label.surface is not None:
            LOG.debug('Blitting name %s: %s', index, self.workspaces[index].name)
            self.screen_image.blit(self.workspaces[index].tile.label.surface,
                                   self.workspaces[index].tile.label.rect)

    def blit_changes(self):
        LOG.info('Blitting tiles')
        #for index in [workspace.num for workspace in self.workspaces]:
        for index in range(1, CONF.workspaces + 1):
            LOG.debug('Checking tile %s (%s) for blitting', index, self.workspaces[index].name)
            tile = self.workspaces[index].tile
            tile.update()
            if self.last_shown is None or \
                    (tile.last['any'] is not None and tile.last['any'] > self.last_shown):
                self.blit_tile(index)
                self.blit_name(index)

    def get_mouse_tile(self, mpos):
        for tile in [self.workspaces[workspace].tile for workspace in self.workspaces]:
            if tile.rect.collidepoint(mpos):
                return tile.workspace.index
        return None

    def get_keyboard_tile(self, kbdmove):
        if self.active_tile is None:
            active_tile = 1
        else:
            active_tile = self.active_tile

        if kbdmove[0] != 0:
            active_tile += kbdmove[0]
        elif kbdmove[1] != 0:
            active_tile += kbdmove[1] * CONF.grid_x

        if active_tile > CONF.workspaces:
            return active_tile - CONF.workspaces
        elif active_tile <= 0:
            return active_tile + CONF.workspaces
        else:
            return active_tile

    def update_ui(self, new_active_tile):
        if new_active_tile != self.active_tile:
            if self.active_tile is not None:
                self.blit_tile(self.active_tile, target=self.screen)
                pygame.display.update(self.workspaces[self.active_tile].tile.rect)
            if new_active_tile is not None:
                self.blit_tile(new_active_tile, active=True, target=self.screen)
                pygame.display.update(self.workspaces[new_active_tile].tile.rect)
            self.active_tile = new_active_tile

    def do_jump(self):
        if self.active_tile is None:
            return False

        if self.workspaces[self.active_tile].name is not None:
            self.con.command('workspace ' + str(self.workspaces[self.active_tile].name))
            LOG.info('Switching to known workspace %s', self.workspaces[self.active_tile].name)
            return True

        return False

    def process_input(self):
        use_mouse = False
        pygame.event.clear()
        new_active_tile = self.get_mouse_tile(pygame.mouse.get_pos())
        while self._running.is_set() and pygame.display.get_init():
            jump = False
            kbdmove = (0, 0)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    LOG.info('UI closed by i3')
                    self._running.clear()
                elif event.type == pygame.MOUSEMOTION:
                    use_mouse = True
                elif event.type == pygame.KEYDOWN:
                    use_mouse = False

                    if event.key == pygame.K_UP or event.key == pygame.K_k:
                        kbdmove = (0, -1)
                    elif event.key == pygame.K_DOWN or event.key == pygame.K_j:
                        kbdmove = (0, 1)
                    elif event.key == pygame.K_LEFT or event.key == pygame.K_h:
                        kbdmove = (-1, 0)
                    elif event.key == pygame.K_RIGHT or event.key == pygame.K_l:
                        kbdmove = (1, 0)
                    elif event.key == pygame.K_RETURN:
                        jump = True
                    elif event.key == pygame.K_ESCAPE:
                        LOG.info('UI closed by Escape key')
                        self._running.clear()
                    pygame.event.clear()
                    break

                elif event.type == pygame.MOUSEBUTTONUP:
                    use_mouse = True
                    if event.button == 1:
                        jump = True
                    pygame.event.clear()
                    break

            if use_mouse:
                new_active_tile = self.get_mouse_tile(pygame.mouse.get_pos())

            elif kbdmove != (0, 0):
                new_active_tile = self.get_keyboard_tile(kbdmove)

            if jump and self.do_jump():
                break

            self.update_ui(new_active_tile)
            pygame.time.wait(25)

        if not jump:
            LOG.info('Selection canceled, jumping to last active workspace %s',
                          self.workspaces[self.workspaces.get_active_workspace()].name)
            self.con.command('workspace ' +
                             self.workspaces[self.workspaces.get_active_workspace()].name)
