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

CONFIG_FILE = os.path.join(xdg_config_home, 'i3expo', 'config')
SCREENSHOT_LIB = 'prtscn.so'
SCREENSHOT_LIB_PATH = os.path.dirname(os.path.abspath(__file__)) + os.path.sep + SCREENSHOT_LIB
GRAB = ctypes.CDLL(SCREENSHOT_LIB_PATH)
GRAB.getScreen.argtypes = []
BLACKLIST = ['i3expod.py', None]

logging.basicConfig(format='%(asctime)s.%(msecs)03d - %(name)4s %(levelname)-8s: %(message)s',
                    datefmt='%T', level=logging.DEBUG)

pygame.display.init()
DEFAULTS = {
    'Capture': {
        'screenshot_width':            pygame.display.Info().current_w,
        'screenshot_height':           pygame.display.Info().current_h,
        'screenshot_offset_x':         0,
        'screenshot_offset_y':         0,
        'forced_update_interval_sec':  5.0,
        'min_update_interval_sec':     0.5
    },

    'UI': {
        'window_width':                pygame.display.Info().current_w,
        'window_height':               pygame.display.Info().current_h,
        'workspaces':                  9,
        'grid_x':                      3,
        'grid_y':                      3,
        'padding_percent_x':           5,
        'padding_percent_y':           5,
        'spacing_percent_x':           5,
        'spacing_percent_y':           5,
        'frame_width_px':              5,
        'highlight_percentage':        20
    },

    'Colors': {
        'bgcolor':                     'gray20',
        'frame_active_color':          '#3b4f8a',
        'frame_inactive_color':        '#43747b',
        'frame_unknown_color':         '#c8986b',
        'frame_empty_color':           'gray60',
        'frame_nonexistant_color':     'gray30',
        'tile_active_color':           '#5a6da4',
        'tile_inactive_color':         '#93afb3',
        'tile_unknown_color':          '#ffe6d0',
        'tile_empty_color':            'gray80',
        'tile_nonexistant_color':      'gray40',
        'names_color':                 'white'
    },

    'Fonts': {
        'names_font':                  'sans-serif',
        'names_fontsize':              25
    },

    'Flags': {
        'names_show':                  True,
        'thumb_stretch':               False,
        'switch_to_empty_workspaces':  False
    },

    'Workspaces': {
    }
}
pygame.display.quit()


def strict_float(raw):
    try:
        int(raw)
    except ValueError:
        return float(raw)
    raise ValueError


def strict_bool(raw):
    if raw == 'True':
        return True
    if raw == 'False':
        return False
    raise ValueError


def read_config():
    config = configparser.ConfigParser(converters={
        'color': pygame.Color,
        'float': strict_float,
        'boolean': strict_bool})

    config.read_dict(DEFAULTS)

    if os.path.exists(CONFIG_FILE):
        logging.info('Read config file in %s', CONFIG_FILE)
        config.read(CONFIG_FILE)
    else:
        root_dir = os.path.dirname(CONFIG_FILE)
        if not os.path.exists(root_dir):
            os.makedirs(root_dir)
        logging.warning('Config file in %s missing, created with defaults', CONFIG_FILE)
        with open(CONFIG_FILE, 'w') as config_file:
            config.write(config_file)

    conf = SimpleNamespace()
    value_order = [config.getfloat, config.getint, config.getcolor, config.getboolean, config.get]

    for group in ['Capture', 'UI', 'Fonts', 'Flags', 'Colors']:
        for item in config[group]:
            for func in value_order:
                try:
                    setattr(conf, item, func(group, item))
                    break
                except ValueError:
                    pass
            if item not in dir(conf):
                raise ValueError("Invalid config value for " + item)

    setattr(conf, 'workspace_names', {})
    for item in config['Workspaces']:
        if item[:10] == 'workspace_':
            conf.workspace_names[int(item[10:])] = config.get('Workspaces', item)
        else:
            raise ValueError("Invalid config variable: " + item)

    return conf


class Updater(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.daemon = True

        self.log = logging.getLogger('updt')

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
        self.log.info('i3ipc thread running')

        self._stop.wait()

    def reinit(self):
        self.log.warning('Reinitializing updater')
        self.read_config()
        self.init_knowledge()

    def destroy(self):
        self.log.warning('Shutting down updater')
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
        self.log.info('Initializing workspace knowledge')
        self.knowledge = {}
        for workspace in self.con.get_tree().workspaces():
            if workspace.num not in self.knowledge.keys():
                self.init_workspace(workspace)

        self.active_workspace = self.con.get_tree().find_focused().workspace().num

    def stop_timer(self, quiet=False):
        if not quiet:
            self.log.info('Stopping forced background updates')
        try:
            self.timer.cancel()
        except AttributeError:
            pass

    def start_timer(self, quiet=False):
        if not quiet:
            self.log.info('Starting forced background updates')
        self.timer = Timer(self.conf.forced_update_interval_sec, self.update)
        self.timer.start()

    def set_new_update_timer(self):
        self.log.info('Resetting update timer')
        self.stop_timer(quiet=True)
        self.start_timer(quiet=True)

    def data_older_than(self, what):
        old = time.time() - self.knowledge[self.active_workspace]['last-update'] > what
        self.log.debug('Screenshot data for workspace %s is%solder than %ss',
                       self.active_workspace, (' not ' if not old else ' '), what)
        return old

    def update(self, ipc=None, stack_frame=None):
        try:
            if stack_frame.container.window_class in BLACKLIST:
                self.log.debug('Update check from %s discarded',
                               stack_frame.container.window_class)
                return False
            self.log.debug('Update check triggered by %s: %s',
                           stack_frame.change, stack_frame.container.window_class)
        except AttributeError:
            self.log.debug('Update check triggered manually')
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
            self.log.debug('Fetching update data for workspace %s', self.active_workspace)
            screenshot = self.grab_screen()
            self.knowledge[self.active_workspace]['screenshot'] = screenshot
            self.knowledge[self.active_workspace]['last-update'] = time.time()

        self.set_new_update_timer()
        return True

    def active_workspace_state_has_changed(self):
        state = ()
        for cont in self.con.get_tree().find_focused().workspace().leaves():
            state += ((cont.id, cont.rect.x, cont.rect.y, cont.rect.width, cont.rect.height),)

        if self.knowledge[self.active_workspace]['state'] == state:
            self.log.debug('Workspace %s has not changed', self.active_workspace)
            return False

        self.knowledge[self.active_workspace]['state'] = state
        self.log.debug('Workspace %s has changed', self.active_workspace)
        return True

    def read_config(self):
        self.log.warning('Reading config file')
        self.conf = read_config()

    def grab_screen(self):
        self.log.debug('Taking a screenshot, probably of workspace %s', self.active_workspace)

        width = self.conf.screenshot_width - self.conf.screenshot_offset_x
        height = self.conf.screenshot_height - self.conf.screenshot_offset_y
        size = width * height
        objlength = size * 3

        result = (ctypes.c_ubyte * objlength)()

        GRAB.getScreen(self.conf.screenshot_offset_x, self.conf.screenshot_offset_y,
                       width, height, result)
        self.log.debug('Screenshot taken, probably of workspace %s', self.active_workspace)
        return (width, height, result)

    def lock(self):
        self._running.clear()
        self.log.info('Pausing updater')

    def unlock(self):
        self._running.set()
        self.set_new_update_timer()
        self.log.info('Starting updater')


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

        self.log = logging.getLogger('intf')

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

        self.con.command('workspace i3expo-temporary-workspace')

        self.blit_changes()

        self.log.info('UI updated')

        self.screen = pygame.display.set_mode(self.windowsize, pygame.RESIZABLE)
        pygame.display.set_caption('i3expo')
        self.screen.blit(self.screen_image.convert_alpha(), (0, 0))
        pygame.display.flip()
        self.last_shown = time.time()
        self.log.info('UI displayed')

        self.process_input()

        self._running.clear()
        self.log.debug('Pygame input processed')

        pygame.display.quit()
        time.sleep(0.1)  # TODO: Get rid of this - how?
        self.log.debug('Pygame window closed')
        self.updater.unlock()

    def prepare_ui(self):
        self.log.warning('Preparing UI')

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

    def destroy(self):
        self.log.warning('Shutting down UI')
        self._stop.set()

    def toggle(self):
        if self._running.is_set():
            self.log.info('Hiding UI')
            self._running.clear()
        else:
            self.log.info('Showing UI')
            self._running.set()
            self.show_ui()

    def read_config(self):
        self.log.info('Reading config file')
        self.conf = read_config()

    def prepare_missing(self):
        self.log.debug('Preparing "Screenshot missing" icon')
        self.missing = pygame.Surface((150, 200), pygame.SRCALPHA, 32)
        question_mark = pygame.font.SysFont('sans-serif', 150).render('?', True, (150, 150, 150))
        question_mark_size = question_mark.get_rect().size
        origin_x = round((150 - question_mark_size[0])/2)
        origin_y = round((200 - question_mark_size[1])/2)
        self.missing.blit(question_mark, (origin_x, origin_y))

    def prepare_tiles(self):
        self.log.debug('Preparing UI tiles')
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
        self.log.debug('Blitting tile %s', index)
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

            self.log.debug('Blitting name %s: %s', index, name)

            name = font.render(name, True, self.conf.names_color)
            name_width = name.get_rect().size[0]
            name_x = self.tiles[index]['ul'][0] + round((self.tile_outer_width - name_width) / 2)
            name_y = self.tiles[index]['ul'][1] + round(self.tile_outer_height * 1.02)
            self.screen_image.blit(name, (name_x, name_y))

    def blit_changes(self):
        self.log.info('Blitting tiles')
        for iter_y in range(self.conf.grid_y):
            for iter_x in range(self.conf.grid_x):
                index = iter_y * self.conf.grid_x + iter_x + 1
                blit = False
                if index not in self.updater.knowledge.keys():
                    if self.last_shown < 0 or self.tiles[index]['drawn']:
                        blit = True
                else:
                    if self.updater.knowledge[index]['last-update'] > self.last_shown:
                        blit = True
                if blit:
                    self.blit_tile(index)
                    self.blit_name(index)

    def get_hovered_tile(self, mpos):
        for tile in self.tiles:
            if (mpos[0] >= self.tiles[tile]['ul'][0] and
                    mpos[0] <= self.tiles[tile]['br'][0] and
                    mpos[1] >= self.tiles[tile]['ul'][1] and
                    mpos[1] <= self.tiles[tile]['br'][1]):
                return tile
        return None

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
            self.log.info('Switching to known workspace %s',
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
                self.log.info('Switching to predefined workspace %s', defined_name)
                return True
        return False

    def process_input(self):
        use_mouse = True
        while self._running.is_set() and pygame.display.get_init():
            jump = False
            kbdmove = (0, 0)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
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
                self.active_tile = self.get_hovered_tile(pygame.mouse.get_pos())

            elif kbdmove != (0, 0):
                if self.active_tile is None:
                    self.active_tile = 1
                if kbdmove[0] != 0:
                    self.active_tile += kbdmove[0]
                elif kbdmove[1] != 0:
                    self.active_tile += kbdmove[1] * self.conf.grid_x
                if self.active_tile > self.conf.workspaces:
                    self.active_tile -= self.conf.workspaces
                elif self.active_tile < 0:
                    self.active_tile += self.conf.workspaces

            if jump and self.do_jump():
                break

            self.update_ui()
            pygame.time.wait(25)

        if not jump:
            self.log.info('Selection canceled, jumping to last active workspace %s',
                          self.updater.knowledge[self.updater.active_workspace]['name'])
            self.con.command('workspace ' +
                             self.updater.knowledge[self.updater.active_workspace]['name'])


def sig_hup(event, stack_frame):
    del event
    del stack_frame
    logging.info('SIGHUP received')
    updater.read_config()
    interface.destroy()


def sig_usr1(event, stack_frame):
    del event
    del stack_frame
    logging.info('SIGUSR1 received')
    interface.toggle()


def sig_int(event, stack_frame):
    del event
    del stack_frame
    logging.info('SIGINT received')
    updater.destroy()
    interface.destroy()
    logging.warning('Shutting down')
    sys.exit(0)


if __name__ == '__main__':
    signal.signal(signal.SIGHUP, sig_hup)
    signal.signal(signal.SIGUSR1, sig_usr1)
    signal.signal(signal.SIGINT, sig_int)

    logging.info('Setting up updater')
    updater = Updater()
    updater.start()
    logging.info('Initializing interface')
    interface = Interface(updater)
    interface.start()
    logging.warning('Setup finished')

    while True:
        time.sleep(10)
