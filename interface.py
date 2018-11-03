#!/usr/bin/python3

import time
import logging
from threading import Thread, Event

import pygame
import i3ipc
from PIL import Image

from config import read_config

LOG = logging.getLogger('intf')


def process_img(raw_img):
    try:
        pil = Image.frombuffer('RGB', (raw_img[0], raw_img[1]), raw_img[2], 'raw', 'RGB', 0, 1)
    except TypeError:
        return None
    return pygame.image.fromstring(pil.tobytes(), pil.size, pil.mode)


class Interface(Thread):
    def __init__(self, updater_instance):
        Thread.__init__(self)
        self.daemon = True

        self.read_config()
        self.con = i3ipc.Connection()
        self.updater = updater_instance

        self._running = Event()
        self._stop = Event()

        self.last_shown = -1

        self.windowsize = (self.conf.window_width, self.conf.window_height)
        self.screen_image = pygame.Surface(self.windowsize)
        self.screen = None

        self.tiles = {}
        self.active_tile = None
        self.missing = None
        self.lightmask = None

        self.prepare_ui()

    def run(self):
        self._stop.wait()

    def show_ui(self):
        self.updater.lock()

        self.active_tile = None

        self.con.command('workspace i3expo-temporary-workspace')

        self.blit_changes()

        LOG.info('UI updated')

        self.screen = pygame.display.set_mode(self.windowsize, pygame.RESIZABLE)
        pygame.display.set_caption('i3expo')
        self.screen.blit(self.screen_image.convert_alpha(), (0, 0))
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
        pygame.font.init()

        self.total_width = self.windowsize[0]
        self.total_height = self.windowsize[1]

        self.screen_padding_x = round(self.total_width * self.conf.padding_percent_x / 100)
        self.screen_padding_y = round(self.total_height * self.conf.padding_percent_y / 100)

        self.tile_spacing_x = round(self.total_width * self.conf.spacing_percent_x / 100)
        self.tile_spacing_y = round(self.total_height * self.conf.spacing_percent_y / 100)

        self.tile_outer_width = round((self.total_width - 2 * self.screen_padding_x -
                                       self.tile_spacing_x * (self.conf.grid_x - 1)) /
                                      self.conf.grid_x)
        self.tile_outer_height = round((self.total_height - 2 * self.screen_padding_y -
                                        self.tile_spacing_y * (self.conf.grid_y - 1)) /
                                       self.conf.grid_y)
        self.tile_outer_size = (self.tile_outer_width, self.tile_outer_height)

        self.tile_inner_width = self.tile_outer_width - 2 * self.conf.frame_width_px
        self.tile_inner_height = self.tile_outer_height - 2 * self.conf.frame_width_px
        self.tile_inner_size = (self.tile_inner_width, self.tile_inner_height)

        self.screen_image.fill(self.conf.bgcolor)

        self.prepare_missing()
        self.prepare_lightmask()
        self.prepare_tiles()

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

    def read_config(self):
        LOG.info('Reading config file')
        self.conf = read_config()

    def prepare_missing(self):
        LOG.debug('Preparing "Screenshot missing" icon')
        self.missing = pygame.Surface((150, 200), pygame.SRCALPHA, 32)
        question_mark = pygame.font.SysFont('sans-serif', 150).render('?', True, (150, 150, 150))
        question_mark_size = question_mark.get_rect().size
        origin_x = round((150 - question_mark_size[0])/2)
        origin_y = round((200 - question_mark_size[1])/2)
        self.missing.blit(question_mark, (origin_x, origin_y))

    def prepare_tiles(self):
        LOG.debug('Preparing UI tiles')
        for idx_x in range(self.conf.grid_x):
            for idx_y in range(self.conf.grid_y):
                origin_x = (self.screen_padding_x +
                            (self.tile_outer_width + self.tile_spacing_x) * idx_x)
                origin_y = (self.screen_padding_y +
                            (self.tile_outer_height + self.tile_spacing_y) * idx_y)

                u_l = (origin_x, origin_y)
                b_r = (origin_x + self.tile_outer_width, origin_y + self.tile_outer_height)

                tile = {
                    'active': False,
                    'mouseoff': None,
                    'mouseon': None,
                    'ul': u_l,
                    'br': b_r,
                    'drawn': False
                }

                self.tiles[idx_y * self.conf.grid_x + idx_x + 1] = tile

    def get_tile_data(self, index):
        tile_color = None
        frame_color = None
        image = None

        if index in self.updater.knowledge.keys() and self.updater.knowledge[index]['state']:
            if self.updater.knowledge[index]['screenshot']:
                if self.updater.active_workspace == index:
                    tile_color = self.conf.tile_active_color
                    frame_color = self.conf.frame_active_color
                    image = process_img(self.updater.knowledge[index]['screenshot'])
                else:
                    tile_color = self.conf.tile_inactive_color
                    frame_color = self.conf.frame_inactive_color
                    image = process_img(self.updater.knowledge[index]['screenshot'])
            else:
                tile_color = self.conf.tile_unknown_color
                frame_color = self.conf.frame_unknown_color
                image = self.missing
        else:
            if index <= self.conf.workspaces:
                tile_color = self.conf.tile_empty_color
                frame_color = self.conf.frame_empty_color
                image = None
            else:
                tile_color = self.conf.tile_nonexistant_color
                frame_color = self.conf.frame_nonexistant_color
                image = None
        return tile_color, frame_color, image

    def fit_image(self, image):
        if self.conf.thumb_stretch:
            image = pygame.transform.smoothscale(image, self.tile_inner_size)
            offset_x = 0
            offset_y = 0
        else:
            image_size = image.get_rect().size
            image_x = image_size[0]
            image_y = image_size[1]
            ratio_x = self.tile_inner_width / image_x
            ratio_y = self.tile_inner_height / image_y
            if ratio_x < ratio_y:
                result_x = self.tile_inner_width
                result_y = round(ratio_x * image_y)
                offset_x = 0
                offset_y = round((self.tile_inner_height - result_y) / 2)
            else:
                result_x = round(ratio_y * image_x)
                result_y = self.tile_inner_height
                offset_x = round((self.tile_inner_width - result_x) / 2)
                offset_y = 0
            image = pygame.transform.smoothscale(image, (result_x, result_y))
        return offset_x, offset_y, image

    def prepare_lightmask(self):
        self.lightmask = pygame.Surface(self.tile_outer_size, pygame.SRCALPHA, 32)
        self.lightmask.fill((255, 255, 255, 255 * self.conf.highlight_percentage / 100))

    def generate_lightmask(self, index):
        mouseon = self.tiles[index]['mouseoff'].copy()
        mouseon.blit(self.lightmask, (0, 0))
        self.tiles[index]['mouseon'] = mouseon

    def blit_tile(self, index):
        LOG.debug('Blitting tile %s', index)
        tile_color, frame_color, image = self.get_tile_data(index)

        tile = pygame.Surface((self.tile_outer_width, self.tile_outer_height))
        tile.fill(frame_color)
        tile.fill(tile_color,
                  (
                      self.conf.frame_width_px,
                      self.conf.frame_width_px,
                      self.tile_inner_width,
                      self.tile_inner_height
                  ))

        if image:
            offset_x, offset_y, image = self.fit_image(image)
            tile.blit(image,
                      (
                          self.conf.frame_width_px + offset_x,
                          self.conf.frame_width_px + offset_y
                      ))
            self.tiles[index]['drawn'] = True
        else:
            self.tiles[index]['drawn'] = False

        self.tiles[index]['mouseoff'] = tile
        self.screen_image.blit(tile, self.tiles[index]['ul'])
        self.generate_lightmask(index)

    def blit_name(self, index):
        font = pygame.font.SysFont(self.conf.names_font, self.conf.names_fontsize)
        defined_name = False
        try:
            defined_name = self.conf.workspace_names[index]
        except KeyError:
            pass

        if self.conf.names_show and (index in self.updater.knowledge.keys() or defined_name):
            if not defined_name:
                name = self.updater.knowledge[index]['name']
            else:
                name = defined_name

            LOG.debug('Blitting name %s: %s', index, name)

            name = font.render(name, True, self.conf.names_color)
            name_width = name.get_rect().size[0]
            name_x = self.tiles[index]['ul'][0] + round((self.tile_outer_width - name_width) / 2)
            name_y = self.tiles[index]['ul'][1] + round(self.tile_outer_height * 1.02)
            self.screen_image.blit(name, (name_x, name_y))

    def blit_changes(self):
        LOG.info('Blitting tiles')
        for iter_y in range(self.conf.grid_y):
            for iter_x in range(self.conf.grid_x):
                index = iter_y * self.conf.grid_x + iter_x + 1
                blit = False
                if index not in self.updater.knowledge.keys():
                    if self.last_shown < 0 or self.tiles[index]['drawn']:
                        blit = True
                else:
                    if self.updater.knowledge[index]['last-update'] > self.last_shown or \
                            not self.updater.knowledge[index]['state']:
                        blit = True
                if blit:
                    self.blit_tile(index)
                    self.blit_name(index)

    def get_mouse_tile(self, mpos):
        for tile in self.tiles:
            if (mpos[0] >= self.tiles[tile]['ul'][0] and
                    mpos[0] <= self.tiles[tile]['br'][0] and
                    mpos[1] >= self.tiles[tile]['ul'][1] and
                    mpos[1] <= self.tiles[tile]['br'][1]):
                return tile
        return None

    def get_keyboard_tile(self, kbdmove):
        if self.active_tile is None:
            active_tile = 1
        else:
            active_tile = self.active_tile

        if kbdmove[0] != 0:
            active_tile += kbdmove[0]
        elif kbdmove[1] != 0:
            active_tile += kbdmove[1] * self.conf.grid_x

        if active_tile > self.conf.workspaces:
            return active_tile - self.conf.workspaces
        elif active_tile <= 0:
            return active_tile + self.conf.workspaces
        else:
            return active_tile

    def update_ui(self):
        for tile in self.tiles:
            if self.tiles[tile]['active'] and not tile == self.active_tile:
                self.screen.blit(self.tiles[tile]['mouseoff'],
                                 self.tiles[tile]['ul'])
                self.tiles[tile]['active'] = False
                pygame.display.update((self.tiles[tile]['ul'],
                                       self.tiles[tile]['br']))

        if self.active_tile and not self.tiles[self.active_tile]['active']:
            self.screen.blit(self.tiles[self.active_tile]['mouseon'],
                             self.tiles[self.active_tile]['ul'])
            self.tiles[self.active_tile]['active'] = True
            pygame.display.update((self.tiles[self.active_tile]['ul'],
                                   self.tiles[self.active_tile]['br']))

    def do_jump(self):
        if self.active_tile in self.updater.knowledge.keys():
            self.con.command('workspace ' + str(self.updater.knowledge[self.active_tile]['name']))
            LOG.info('Switching to known workspace %s',
                          self.updater.knowledge[self.active_tile]['name'])
            return True
        if self.conf.switch_to_empty_workspaces:
            defined_name = False
            try:
                defined_name = self.conf.workspace_names[self.active_tile]
            except KeyError:
                pass
            if defined_name:
                self.con.command('workspace ' + defined_name)
                LOG.info('Switching to predefined workspace %s', defined_name)
                return True
        return False

    def process_input(self):
        use_mouse = False
        pygame.event.clear()
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
                self.active_tile = self.get_mouse_tile(pygame.mouse.get_pos())

            elif kbdmove != (0, 0):
                self.active_tile = self.get_keyboard_tile(kbdmove)

            if jump and self.do_jump():
                break

            self.update_ui()
            pygame.time.wait(25)

        if not jump:
            LOG.info('Selection canceled, jumping to last active workspace %s',
                          self.updater.knowledge[self.updater.active_workspace]['name'])
            self.con.command('workspace ' +
                             self.updater.knowledge[self.updater.active_workspace]['name'])
