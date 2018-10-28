#!/usr/bin/python3

"""
Show a configurable expose of i3 workspaces

Dependencies: i3ipc, pygame

Compile prtscn.cc as per instructions in the source code and place it with i3expo.py.
"""

import ctypes
import os
import configparser
import signal
import sys
import time
from types import SimpleNamespace
from functools import partial
from threading import Thread

import pygame
import i3ipc
from PIL import Image
from xdg.BaseDirectory import xdg_config_home

from debounce import Debounce

C = SimpleNamespace()

global_updates_running = True
global_knowledge = {'active': -1}

CONFIG_FILE = os.path.join(xdg_config_home, 'i3expo', 'config')
SCREENSHOT_LIB = 'prtscn.so'
SCREENSHOT_LIB_PATH = os.path.dirname(os.path.abspath(__file__)) + os.path.sep + SCREENSHOT_LIB
GRAB = ctypes.CDLL(SCREENSHOT_LIB_PATH)
GRAB.getScreen.argtypes = []
BLACKLIST = ['i3expod.py']

def con():
    """ Return a connection to the i3 IPC """
    return i3ipc.Connection()

def signal_quit(sig, stack_frame):
    """ Exit the application when SIGINT is received """
    del sig
    del stack_frame
    print('Shutting down...')
    pygame.display.quit()
    pygame.quit()
    con().main_quit()
    sys.exit(0)


def signal_reload(sig, stack_frame):
    """ Reload the configuration when SIGHUP is received """
    del sig
    del stack_frame
    read_config()


def signal_toggle_ui(sig, stack_frame):
    """ Show the expo UI when SIGUSR1 is received """
    global global_updates_running
    global global_knowledge

    del sig
    del stack_frame

    if not global_updates_running:
        global_updates_running = True
    else:
        current_workspace = con().get_tree().find_focused().workspace()
        global_knowledge['active'] = current_workspace.num
        con().command('workspace i3expod-temporary-workspace')
        global_updates_running = False
        updater_debounced.reset()
        ui = Interface(C)

def strict_float(raw):
    """ Returns float if passed exactly n+.n+, raises ValueError otherwise

    Arguments:
    str raw: The value to be checked

    """
    try:
        int(raw)
    except ValueError:
        return float(raw)
    raise ValueError


def strict_bool(raw):
    """ Returns bool if passed exactly True or False, raises ValueError otherwise

    Arguments:
    str raw: The value to be checked
    """
    if raw == 'True':
        return True
    if raw == 'False':
        return False
    raise ValueError


def read_config():
    """ Set default config values, read config file (or write if missing) and interpret results

    Sets:
    global C: The configurations SimpleNamespace
    """
    global C

    config = configparser.ConfigParser(converters={
        'color': pygame.Color,
        'float': strict_float,
        'boolean': strict_bool})

    pygame.display.init()
    disp_info = pygame.display.Info()
    config.read_dict({
        'Capture': {
            'screenshot_width'           : disp_info.current_w,
            'screenshot_height'          : disp_info.current_h,
            'screenshot_offset_x'        : 0,
            'screenshot_offset_y'        : 0,
            'forced_update_interval_sec' : 10.0,
            'debounce_period_sec'        : 1.0
        },

        'UI': {
            'window_width'               : disp_info.current_w,
            'window_height'              : disp_info.current_h,
            'workspaces'                 : 9,
            'grid_x'                     : 3,
            'grid_y'                     : 3,
            'padding_percent_x'          : 5,
            'padding_percent_y'          : 5,
            'spacing_percent_x'          : 5,
            'spacing_percent_y'          : 5,
            'frame_width_px'             : 5,
            'highlight_percentage'       : 20
        },

        'Colors': {
            'bgcolor'                    : 'gray20',
            'frame_active_color'         : '#3b4f8a',
            'frame_inactive_color'       : '#43747b',
            'frame_unknown_color'        : '#c8986b',
            'frame_empty_color'          : 'gray60',
            'frame_nonexistant_color'    : 'gray30',
            'tile_active_color'          : '#5a6da4',
            'tile_inactive_color'        : '#93afb3',
            'tile_unknown_color'         : '#ffe6d0',
            'tile_empty_color'           : 'gray80',
            'tile_nonexistant_color'     : 'gray40',
            'names_color'                : 'white'
        },

        'Fonts': {
            'names_font'                 : 'sans-serif',
            'names_fontsize'             : 25
        },

        'Flags': {
            'names_show'                 : True,
            'thumb_stretch'              : False,
            'switch_to_empty_workspaces' : False
        },

        'Workspaces': {
        }
    })
    pygame.display.quit()

    root_dir = os.path.dirname(CONFIG_FILE)
    if not os.path.exists(root_dir):
        os.makedirs(root_dir)

    if os.path.exists(CONFIG_FILE):
        config.read(CONFIG_FILE)
    else:
        with open(CONFIG_FILE, 'w') as config_file:
            config.write(config_file)

    value_order = [config.getfloat, config.getint, config.getcolor, config.getboolean, config.get]

    for group in ['Capture', 'UI', 'Fonts', 'Flags', 'Colors']:
        for item in config[group]:
            for func in value_order:
                try:
                    setattr(C, item, func(group, item))
                    break
                except ValueError:
                    pass
            if item not in dir(C):
                raise ValueError("Invalid config value for " + item)

    setattr(C, 'workspace_names', {})
    for item in config['Workspaces']:
        if item[:10] == 'workspace_':
            C.workspace_names[int(item[10:])] = config.get('Workspaces', item)
        else:
            raise ValueError("Invalid config variable: " + item)

def grab_screen():
    """ Return a screenshot as a byte array

    Returns:
    (width, height, result) -- width, height, image byte array
    """
    width = C.screenshot_width - C.screenshot_offset_x
    height = C.screenshot_height - C.screenshot_offset_y
    size = width * height
    objlength = size * 3

    result = (ctypes.c_ubyte * objlength)()

    GRAB.getScreen(C.screenshot_offset_x, C.screenshot_offset_y, width, height, result)
    return (width, height, result)




def make_active(workspace):
    """ Check if active namespace is already known

    Arguments:
    workspace -- The current workspace
    """
    global_knowledge['active'] = workspace.num


def init_knowledge():
    """ Initialize workspace dict

    Sets:
    global_knowledge -- What we know about the state of the WM
    """
    for workspace in con().get_tree().workspaces():
        if workspace.num not in global_knowledge:
            global_knowledge[workspace.num] = {
                'name'        : workspace.name,
                'screenshot'  : None,
                'last-update' : 0.0,
                'state'       : 0,
                'windows'     : {}
            }


def tree_has_changed(focused_ws):
    """ Check if there are any changes in the current workspace

    Arguments:
    focused_ws -- Current workspace

    Returns:
    bool -- Whether there are any changes
    """
    state = 0
    for cont in focused_ws.leaves():
        focus = 31 if cont.focused else 0
        state += cont.id % (cont.rect.x + cont.rect.y + cont.rect.width + cont.rect.height + focus)

    if global_knowledge[focused_ws.num]['state'] == state:
        return False
    global_knowledge[focused_ws.num]['state'] = state

    return True


def should_update(rate_limit_period, focused_con, focused_ws, force):
    if not global_updates_running:
        return False
    if rate_limit_period is not None and \
            time.time() - global_knowledge[focused_ws.num]['last-update'] <= rate_limit_period:
        return False
    if focused_con.window_class in BLACKLIST:
        return False
    if force:
        tree_has_changed(focused_ws)
        updater_debounced.reset()
        return True
    if not tree_has_changed(focused_ws):
        return False

    return True


def update_state(event=None, rate_limit_period=None, force=False):
    global global_knowledge

    del event

    container_tree = con().get_tree()
    focused_con = container_tree.find_focused()
    focused_ws = focused_con.workspace()

    global_knowledge['active'] = focused_ws.num

    if not should_update(rate_limit_period, focused_con, focused_ws, force):
        return

    wspace_nums = [w.num for w in container_tree.workspaces()]
    deleted = []
    for item in global_knowledge:
        if isinstance(item, int) and item not in wspace_nums:
            deleted.append(item)
    for item in deleted:
        del global_knowledge[item]

    global_knowledge[focused_ws.num]['screenshot'] = grab_screen()
    global_knowledge[focused_ws.num]['last-update'] = time.time()




class Interface(Thread):
    """ The main interface for i3expod """

    def __init__(self, conf):
        """ Init function

        Calculate various dimensions and prepopulate reusable things

        Arguments:
        conf -- The configuration SimpleNamespace
        """
        self.conf = conf

        Thread.__init__(self)
        pygame.display.init()
        pygame.font.init()

        self.screen = pygame.display.set_mode((self.conf.window_width, self.conf.window_height), pygame.RESIZABLE)

        pygame.display.set_caption('i3expo')

        self.total_width = self.screen.get_width()
        self.total_height = self.screen.get_height()

        self.screen_padding_x = round(self.total_width * self.conf.padding_percent_x / 100)
        self.screen_padding_y = round(self.total_height * self.conf.padding_percent_y / 100)

        self.tile_spacing_x = round(self.total_width * self.conf.spacing_percent_x / 100)
        self.tile_spacing_y = round(self.total_height * self.conf.spacing_percent_y / 100)

        self.tile_outer_width = round((self.total_width - 2 * self.screen_padding_x - \
                                        self.tile_spacing_x * (self.conf.grid_x - 1)) / C.grid_x)
        self.tile_outer_height = round((self.total_height - 2 * self.screen_padding_y - \
                                        self.tile_spacing_y * (self.conf.grid_y - 1)) / C.grid_y)

        self.tile_inner_width = self.tile_outer_width - 2 * self.conf.frame_width_px
        self.tile_inner_height = self.tile_outer_height - 2 * self.conf.frame_width_px

        self.screen.fill(self.conf.bgcolor)

        self.prepare_missing()
        self.prepare_tiles()

        self.show()
        self.process_input()

    def prepare_missing(self):
        self.missing = pygame.Surface((150, 200), pygame.SRCALPHA, 32).convert_alpha()
        question_mark = pygame.font.SysFont('sans-serif', 150).render('?', True, (150, 150, 150))
        question_mark_size = question_mark.get_rect().size
        origin_x = round((150 - question_mark_size[0])/2)
        origin_y = round((200 - question_mark_size[1])/2)
        self.missing.blit(question_mark, (origin_x, origin_y))

    def prepare_tiles(self):
        self.tiles = {}
        for idx_x in range(self.conf.grid_x):
            for idx_y in range(self.conf.grid_y):
                origin_x = self.screen_padding_x + (self.tile_outer_width + self.tile_spacing_x) * idx_x
                origin_y = self.screen_padding_y + (self.tile_outer_height + self.tile_spacing_y) * idx_y

                ul = (origin_x, origin_y)
                br = (origin_x + self.tile_outer_width, origin_y + self.tile_outer_height)
                
                tile = {
                    'active': False,
                    'mouseoff': None,
                    'mouseon': None,
                    'ul': ul,
                    'br': br
                }

                self.tiles[idx_y * self.conf.grid_x + idx_x + 1] = tile

    def process_img(self, raw_img):
        """ Process an image byte array for use in PyGame

        Returns:
        pygame.image -- The screenshot
        """
        try:
            pil = Image.frombuffer('RGB', (raw_img[0], raw_img[1]), raw_img[2], 'raw', 'RGB', 0, 1)
        except TypeError:
            return None
        return pygame.image.fromstring(pil.tobytes(), pil.size, pil.mode)

    def get_tile_data(self, index):
        if global_knowledge['active'] == index:
            tile_color = self.conf.tile_active_color
            frame_color = self.conf.frame_active_color
            image = self.process_img(global_knowledge[index]['screenshot'])
        elif index in global_knowledge.keys() and global_knowledge[index]['screenshot']:
            tile_color = self.conf.tile_inactive_color
            frame_color = self.conf.frame_inactive_color
            image = self.process_img(global_knowledge[index]['screenshot'])
        elif index in global_knowledge.keys():
            tile_color = self.conf.tile_unknown_color
            frame_color = self.conf.frame_unknown_color
            image = self.missing
        elif index <= self.conf.workspaces:
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
            image = pygame.transform.smoothscale(image, (self.tile_inner_width, self.tile_inner_height))
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


    def generate_lightmask(self, index, tile):
        lightmask = pygame.Surface((self.tile_outer_width, self.tile_outer_height), pygame.SRCALPHA, 32)
        lightmask.convert_alpha()
        lightmask.fill((255, 255, 255, 255 * C.highlight_percentage / 100))
        mouseon = tile.copy()
        mouseon.blit(lightmask, (0, 0))

        self.tiles[index]['mouseoff'] = tile
        self.tiles[index]['mouseon'] = mouseon


    def blit_tile(self, index):
        tile_color, frame_color, image = self.get_tile_data(index)

        tile = pygame.Surface((self.tile_outer_width, self.tile_outer_height)).convert_alpha()
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

        self.screen.blit(tile, self.tiles[index]['ul'])

        self.generate_lightmask(index, tile)

    def blit_name(self, index):
        font = pygame.font.SysFont(self.conf.names_font, self.conf.names_fontsize)
        defined_name = False
        try:
            defined_name = self.conf.workspace_names[index]
        except KeyError:
            pass

        if self.conf.names_show and (index in global_knowledge.keys() or defined_name):
            if not defined_name:
                name = global_knowledge[index]['name']
            else:
                name = defined_name
            name = font.render(name, True, self.conf.names_color)
            name_width = name.get_rect().size[0]
            name_x = self.tiles[index]['ul'][0] + round((self.tile_outer_width - name_width) / 2)
            name_y = self.tiles[index]['ul'][1] + round(self.tile_outer_height * 1.02)
            self.screen.blit(name, (name_x, name_y))

    def show(self):
        for iter_y in range(C.grid_y):
            for iter_x in range(C.grid_x):
                index = iter_y * self.conf.grid_x + iter_x + 1
                self.blit_tile(index)
                self.blit_name(index)

        pygame.display.flip()

    def get_hovered_tile(self, mpos):
        """ Get the currently hovered UI tile

        Arguments:
        mpos -- mouse position

        Returns:
        tile -- Hovered tile as int or None
        """
        for tile in self.tiles:
            if (mpos[0] >= self.tiles[tile]['ul'][0]
                    and mpos[0] <= self.tiles[tile]['br'][0]
                    and mpos[1] >= self.tiles[tile]['ul'][1]
                    and mpos[1] <= self.tiles[tile]['br'][1]):
                return tile
        return None

    def process_input(self):
        global global_updates_running

        running = True
        use_mouse = True
        while running and not global_updates_running and pygame.display.get_init():
            jump = False
            kbdmove = (0, 0)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.MOUSEMOTION:
                    use_mouse = True
                elif event.type == pygame.KEYDOWN:
                    use_mouse = False

                    if event.key == pygame.K_UP or event.key == pygame.K_k:
                        kbdmove = (0, -1)
                    if event.key == pygame.K_DOWN or event.key == pygame.K_j:
                        kbdmove = (0, 1)
                    if event.key == pygame.K_LEFT or event.key == pygame.K_h:
                        kbdmove = (-1, 0)
                    if event.key == pygame.K_RIGHT or event.key == pygame.K_l:
                        kbdmove = (1, 0)
                    if event.key == pygame.K_RETURN:
                        jump = True
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    pygame.event.clear()
                    break

                elif event.type == pygame.MOUSEBUTTONUP:
                    use_mouse = True
                    if event.button == 1:
                        jump = True
                    pygame.event.clear()
                    break

            if use_mouse:
                active_tile = self.get_hovered_tile(pygame.mouse.get_pos())

            elif kbdmove != (0, 0):
                if active_frame is None:
                    active_frame = 1
                if kbdmove[0] != 0:
                    active_frame += kbdmove[0]
                elif kbdmove[1] != 0:
                    active_frame += kbdmove[1] * self.conf.grid_x
                if active_frame > self.conf.workspaces:
                    active_frame -= self.conf.workspaces
                elif active_frame < 0:
                    active_frame += self.conf.workspaces

            if jump:
                if active_tile in global_knowledge.keys():
                    con().command('workspace ' + str(global_knowledge[active_tile]['name']))
                    break
                if self.conf.switch_to_empty_workspaces:
                    defined_name = False
                    try:
                        defined_name = self.conf.workspace_names[active_tile]
                    except KeyError:
                        pass
                    if defined_name:
                        con().command('workspace ' + defined_name)
                        break

            for tile in self.tiles:
                if self.tiles[tile]['active'] and not tile == active_tile:
                    self.screen.blit(self.tiles[tile]['mouseoff'], self.tiles[tile]['ul'])
                    self.tiles[tile]['active'] = False
                    pygame.display.update((self.tiles[tile]['ul'], self.tiles[tile]['br']))
            if active_tile and not self.tiles[active_tile]['active']:
                self.screen.blit(self.tiles[active_tile]['mouseon'], self.tiles[active_tile]['ul'])
                self.tiles[active_tile]['active'] = True
                pygame.display.update((self.tiles[active_tile]['ul'], self.tiles[active_tile]['br']))

            pygame.time.wait(25)

        if not jump:
            con().command('workspace ' + global_knowledge[global_knowledge['active']]['name'])

        pygame.display.quit()
        pygame.display.init()
        global_updates_running = True


if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_quit)
    signal.signal(signal.SIGTERM, signal_quit)
    signal.signal(signal.SIGHUP, signal_reload)
    signal.signal(signal.SIGUSR1, signal_toggle_ui)

    read_config()
    init_knowledge()
    updater_debounced = Debounce(C.debounce_period_sec, update_state)
    update_state(None)

    con().on('window::move', updater_debounced)
    con().on('window::floating', updater_debounced)
    con().on('window::fullscreen_mode', partial(updater_debounced, force=True))
    con().on('window::focus', updater_debounced)

    i3_thread = Thread(target=con().main)
    i3_thread.daemon = True
    i3_thread.start()

    while True:
        time.sleep(C.forced_update_interval_sec)
        update_state(C.debounce_period_sec, force=True)
