#!/usr/bin/python3

import ctypes
import os
import configparser
import xdg
import pygame
import i3ipc
import copy
import signal
import sys
import traceback
import pprint
import time
from debounce import Debounce
from functools import partial
from threading import Thread
from PIL import Image, ImageDraw
from types import SimpleNamespace

from xdg.BaseDirectory import xdg_config_home

C = SimpleNamespace()

pp = pprint.PrettyPrinter(indent=4)

global_updates_running = True
global_knowledge = {'active': -1}

i3 = i3ipc.Connection()

config_file = os.path.join(xdg_config_home, 'i3expo', 'config')
screenshot_lib = 'prtscn.so'
screenshot_lib_path = os.path.dirname(os.path.abspath(__file__)) + os.path.sep + screenshot_lib
grab = ctypes.CDLL(screenshot_lib_path)
grab.getScreen.argtypes = []
blacklist_classes = ['i3expod.py']

def signal_quit(signal, stack_frame):
    print('Shutting down...')
    pygame.display.quit()
    pygame.quit()
    i3.main_quit()
    sys.exit(0)

def signal_reload(signal, stack_frame):
    read_config()

def should_show_ui():
    return len(global_knowledge) - 1 > 1

def signal_toggle_ui(signal, stack_frame):
    global global_updates_running
    if not global_updates_running:
        global_updates_running = True
    elif should_show_ui():
        current_workspace = i3.get_tree().find_focused().workspace()
        update_workspace(current_workspace)
        i3.command('workspace i3expod-temporary-workspace')
        global_updates_running = False
        updater_debounced.reset()
        ui_thread = Thread(target = show_ui)
        ui_thread.daemon = True
        ui_thread.start()


def strict_float(raw):
    try:
        int(raw)
    except:
        return float(raw)
    raise ValueError


def strict_bool(raw):
    if raw == 'True':
        return True
    elif raw == 'False':
        return False
    else:
        raise ValueError


def read_config():
    global C
    global WS

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

    root_dir = os.path.dirname(config_file)
    if not os.path.exists(root_dir):
        os.makedirs(root_dir)

    if os.path.exists(config_file):
        config.read(config_file)
    else:
        with open(config_file, 'w') as f:
            config.write(f)

    for group in ['Capture', 'UI', 'Fonts', 'Flags', 'Colors']:
        for item in config[group]:
            for func in [config.getfloat, config.getint, config.getcolor, config.getboolean, config.get]:
                try:
                    setattr(C, item, func(group, item))
                    break
                except ValueError:
                    pass
            if item not in dir(C):
                raise ValueError("Invalid config value for " + item)

    WS = {}
    for item in config['Workspaces']:
        if item[:10] == 'workspace_':
            WS[int(item[10:])] = config.get('Workspaces', item)
        else:
            raise ValueError("Invalid config variable: " + item)

def grab_screen():
    x1 = C.screenshot_offset_x
    y1 = C.screenshot_offset_y
    x2 = C.screenshot_width
    y2 = C.screenshot_height

    w, h = x2-x1, y2-y1
    size = w * h
    objlength = size * 3

    result = (ctypes.c_ubyte*objlength)()

    grab.getScreen(x1, y1, w, h, result)
    return (w, h, result)


def process_img(raw_img):
    try:
        pil = Image.frombuffer('RGB', (raw_img[0], raw_img[1]), raw_img[2], 'raw', 'RGB', 0, 1)
    except TypeError:
        return None
    return pygame.image.fromstring(pil.tobytes(), pil.size, pil.mode)


def update_workspace(workspace):
    if workspace.num not in global_knowledge:
        global_knowledge[workspace.num] = {
            'name'        : workspace.name,
            'screenshot'  : None,
            'last-update' : 0.0,
            'state'       : 0,
            'windows'     : {}
        }

    global_knowledge['active'] = workspace.num


def init_knowledge():
    for ws in i3.get_tree().workspaces():
        update_workspace(ws)


def tree_has_changed(focused_ws):
    state = 0
    for con in focused_ws.leaves():
        f = 31 if con.focused else 0  # so focus change can be detected
        state += con.id % (con.rect.x + con.rect.y + con.rect.width + con.rect.height + f)

    if global_knowledge[focused_ws.num]['state'] == state: return False
    global_knowledge[focused_ws.num]['state'] = state

    return True


def should_update(rate_limit_period, focused_con, focused_ws, con_tree, event, force):
    if not global_updates_running: return False
    elif rate_limit_period != None and time.time() - global_knowledge[focused_ws.num]['last-update'] <= rate_limit_period: return False
    elif focused_con.window_class in blacklist_classes: return False
    elif force:
        tree_has_changed(focused_ws)  # call it, as we still want to store changed state
        updater_debounced.reset()
        return True
    elif not tree_has_changed(focused_ws): return False

    return True


def update_state(i3, e=None, rate_limit_period=None, force=False):
    time.sleep(0.2)  # TODO system-specific; configurize?

    container_tree = i3.get_tree()
    focused_con = container_tree.find_focused()
    focused_ws = focused_con.workspace()

    update_workspace(focused_ws)

    if not should_update(rate_limit_period, focused_con, focused_ws, container_tree, e, force): return

    wspace_nums = [w.num for w in container_tree.workspaces()]
    deleted = []
    for n in global_knowledge:
        if type(n) is int and n not in wspace_nums:  # TODO move n-keys to different map, so type(n)=int check wouldn't be necessary?
            deleted.append(n)
    for n in deleted:
        del global_knowledge[n]

    global_knowledge[focused_ws.num]['screenshot'] = grab_screen()
    global_knowledge[focused_ws.num]['last-update'] = time.time()


def get_hovered_tile(mpos, tiles):
    for tile in tiles:
        t = tiles[tile]
        if (mpos[0] >= t['ul'][0]
                and mpos[0] <= t['br'][0]
                and mpos[1] >= t['ul'][1]
                and mpos[1] <= t['br'][1]):
            return tile
    return None


def show_ui():
    global global_updates_running

    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode((C.window_width, C.window_height), pygame.RESIZABLE)
    pygame.display.set_caption('i3expo')

    total_x = screen.get_width()
    total_y = screen.get_height()

    pad_x = round(total_x * C.padding_percent_x / 100)
    pad_y = round(total_y * C.padding_percent_y / 100)

    space_x = round(total_x * C.spacing_percent_x / 100)
    space_y = round(total_y * C.spacing_percent_y / 100)

    shot_outer_x = round((total_x - 2 * pad_x - space_x * (C.grid_x - 1)) / C.grid_x)
    shot_outer_y = round((total_y - 2 * pad_y - space_y * (C.grid_y - 1)) / C.grid_y)

    shot_inner_x = shot_outer_x - 2 * C.frame_width_px 
    shot_inner_y = shot_outer_y - 2 * C.frame_width_px

    offset_delta_x = shot_outer_x + space_x
    offset_delta_y = shot_outer_y + space_y

    screen.fill(C.bgcolor)
    
    missing = pygame.Surface((150,200), pygame.SRCALPHA, 32) 
    missing = missing.convert_alpha()
    qm = pygame.font.SysFont('sans-serif', 150).render('?', True, (150, 150, 150))
    qm_size = qm.get_rect().size
    origin_x = round((150 - qm_size[0])/2)
    origin_y = round((200 - qm_size[1])/2)
    missing.blit(qm, (origin_x, origin_y))

    frames = {}

    font = pygame.font.SysFont(C.names_font, C.names_fontsize)

    for y in range(C.grid_y):
        for x in range(C.grid_x):

            index = y * C.grid_x + x + 1

            frames[index] = {
                    'active': False,
                    'mouseoff': None,
                    'mouseon': None,
                    'ul': (None, None),
                    'br': (None, None)
            }

            if global_knowledge['active'] == index:
                tile_color = C.tile_active_color
                frame_color = C.frame_active_color
                image = process_img(global_knowledge[index]['screenshot'])
            elif index in global_knowledge.keys() and global_knowledge[index]['screenshot']:
                tile_color = C.tile_inactive_color
                frame_color = C.frame_inactive_color
                image = process_img(global_knowledge[index]['screenshot'])
            elif index in global_knowledge.keys():
                tile_color = C.tile_unknown_color
                frame_color = C.frame_unknown_color
                image = missing
            elif index <= C.workspaces:
                tile_color = C.tile_empty_color
                frame_color = C.frame_empty_color
                image = None
            else:
                tile_color = C.tile_nonexistant_color
                frame_color = C.frame_nonexistant_color
                image = None

            origin_x = pad_x + offset_delta_x * x
            origin_y = pad_y + offset_delta_y * y

            frames[index]['ul'] = (origin_x, origin_y)
            frames[index]['br'] = (origin_x + shot_outer_x, origin_y + shot_outer_y)

            screen.fill(frame_color,
                    (
                        origin_x,
                        origin_y,
                        shot_outer_x,
                        shot_outer_y,
                    ))

            screen.fill(tile_color,
                    (
                        origin_x + C.frame_width_px,
                        origin_y + C.frame_width_px,
                        shot_inner_x,
                        shot_inner_y,
                    ))

            if image:
                if C.thumb_stretch:
                    image = pygame.transform.smoothscale(image, (shot_inner_x, shot_inner_y))
                    offset_x = 0
                    offset_y = 0
                else:
                    image_size = image.get_rect().size
                    image_x = image_size[0]
                    image_y = image_size[1]
                    ratio_x = shot_inner_x / image_x
                    ratio_y = shot_inner_y / image_y
                    if ratio_x < ratio_y:
                        result_x = shot_inner_x
                        result_y = round(ratio_x * image_y)
                        offset_x = 0
                        offset_y = round((shot_inner_y - result_y) / 2)
                    else:
                        result_x = round(ratio_y * image_x)
                        result_y = shot_inner_y
                        offset_x = round((shot_inner_x - result_x) / 2)
                        offset_y = 0
                    image = pygame.transform.smoothscale(image, (result_x, result_y))
                screen.blit(image, (origin_x + C.frame_width_px + offset_x, origin_y + C.frame_width_px + offset_y))

            mouseoff = screen.subsurface((origin_x, origin_y, shot_outer_x, shot_outer_y)).copy()
            lightmask = pygame.Surface((shot_outer_x, shot_outer_y), pygame.SRCALPHA, 32)
            lightmask.convert_alpha()
            lightmask.fill((255,255,255,255 * C.highlight_percentage / 100))
            mouseon = mouseoff.copy()
            mouseon.blit(lightmask, (0, 0))

            frames[index]['mouseon'] = mouseon.copy()
            frames[index]['mouseoff'] = mouseoff.copy()

            defined_name = False
            try:
                defined_name = WS[index]
            except:
                pass

            if C.names_show and (index in global_knowledge.keys() or defined_name):
                if not defined_name:
                    name = global_knowledge[index]['name']
                else:
                    name = defined_name
                name = font.render(name, True, C.names_color)
                name_width = name.get_rect().size[0]
                name_x = origin_x + round((shot_outer_x - name_width) / 2)
                name_y = origin_y + shot_outer_y + round(shot_outer_y * 0.02)
                screen.blit(name, (name_x, name_y))

    pygame.display.flip()

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
            mpos = pygame.mouse.get_pos()
            active_frame = get_hovered_tile(mpos, frames)
        elif kbdmove != (0, 0):
            if active_frame == None:
                active_frame = 1
            if kbdmove[0] != 0:
                active_frame += kbdmove[0]
            elif kbdmove[1] != 0:
                active_frame += kbdmove[1] * grid_x
            if active_frame > C.workspaces:
                active_frame -= C.workspaces
            elif active_frame < 0:
                active_frame += C.workspaces
            print(active_frame)

        if jump:
            if active_frame in global_knowledge.keys():
                i3.command('workspace ' + str(global_knowledge[active_frame]['name']))
                break
            if C.switch_to_empty_workspaces:
                defined_name = False
                try:
                    defined_name = WS[active_frame]
                except:
                    pass
                if defined_name:
                    i3.command('workspace ' + defined_name)
                    break

        for frame in frames.keys():
            if frames[frame]['active'] and not frame == active_frame:
                screen.blit(frames[frame]['mouseoff'], frames[frame]['ul'])
                frames[frame]['active'] = False
                pygame.display.update((frames[frame]['ul'], frames[frame]['br']))
        if active_frame and not frames[active_frame]['active']:
            screen.blit(frames[active_frame]['mouseon'], frames[active_frame]['ul'])
            frames[active_frame]['active'] = True
            pygame.display.update((frames[active_frame]['ul'], frames[active_frame]['br']))

        pygame.time.wait(25)

    if not jump:
        i3.command('workspace ' + global_knowledge[global_knowledge['active']]['name'])

    pygame.display.quit()
    pygame.display.init()
    global_updates_running = True

if __name__ == '__main__':

    converters = {'color': pygame.Color, 'float': strict_float, 'boolean': strict_bool}
    config = configparser.ConfigParser(converters = converters)

    signal.signal(signal.SIGINT, signal_quit)
    signal.signal(signal.SIGTERM, signal_quit)
    signal.signal(signal.SIGHUP, signal_reload)
    signal.signal(signal.SIGUSR1, signal_toggle_ui)

    read_config()
    init_knowledge()
    updater_debounced = Debounce(C.debounce_period_sec, update_state)
    update_state(i3, None)

    i3.on('window::move', updater_debounced)
    i3.on('window::floating', updater_debounced)
    i3.on('window::fullscreen_mode', partial(updater_debounced, force = True))
    i3.on('window::focus', updater_debounced)

    i3_thread = Thread(target = i3.main)
    i3_thread.daemon = True
    i3_thread.start()

    while True:
        time.sleep(C.forced_update_interval_sec)
        update_state(i3, C.debounce_period_sec, force = True)
