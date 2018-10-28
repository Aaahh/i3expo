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
from threading import Thread
from PIL import Image, ImageDraw

from xdg.BaseDirectory import xdg_config_home

pp = pprint.PrettyPrinter(indent=4)

global_updates_running = True
global_knowledge = {'active': 0}

i3 = i3ipc.Connection()

config_file = os.path.join(xdg_config_home, 'i3expo', 'config')
screenshot_lib = 'prtscn.so'
screenshot_lib_path = os.path.dirname(os.path.abspath(__file__)) + os.path.sep + screenshot_lib
grab = ctypes.CDLL(screenshot_lib_path)

def signal_quit(signal, frame):
    print("Shutting down...")
    pygame.display.quit()
    pygame.quit()
    i3.main_quit()
    sys.exit(0)

def signal_reload(signal, frame):
    read_config()

def signal_show(signal, frame):
    global global_updates_running
    if not global_updates_running:
        global_updates_running = True
    else:
        current_workspace = i3.get_tree().find_focused().workspace()
        update_workspace(current_workspace)
        i3.command('workspace i3expod-temporary-workspace')
        global_updates_running = False
        ui_thread = Thread(target = show_ui)
        ui_thread.daemon = True
        ui_thread.start()

signal.signal(signal.SIGINT, signal_quit)
signal.signal(signal.SIGTERM, signal_quit)
signal.signal(signal.SIGHUP, signal_reload)
signal.signal(signal.SIGUSR1, signal_show)

def get_color(raw):
    return pygame.Color(raw)

def read_config():
    pygame.display.init()
    disp_info = pygame.display.Info()
    config.read_dict({
        'CONF': {
            'screenshot_width'           : disp_info.current_w,
            'screenshot_height'          : disp_info.current_h,
            'screenshot_offset_x'        : 0,
            'screenshot_offset_y'        : 0,

            'window_width'               : disp_info.current_w,
            'window_height'              : disp_info.current_h,
            'bgcolor'                    : 'gray20',

            'workspaces'                 : 9,
            'grid_x'                     : 3,
            'grid_y'                     : 3,

            'padding_percent_x'          : 5,
            'padding_percent_y'          : 5,
            'spacing_percent_x'          : 5,
            'spacing_percent_y'          : 5,
            'frame_width_px'             : 5,

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

            'names_show'                 : True,
            'names_font'                 : 'sans-serif',
            'names_fontsize'             : 25,
            'names_color'                : 'white',
            'thumb_stretch'              : False,
            'highlight_percentage'       : 20,

            'switch_to_empty_workspaces' : False
        }
    })
    pygame.display.quit()

    root_dir = os.path.dirname(config_file)
    if not os.path.exists(root_dir):
        os.makedirs(root_dir)

    if os.path.exists(config_file):
        print('exists')
        config.read(config_file)
    else:
        with open(config_file, 'w') as f:
            config.write(f)


def grab_screen():
    x1 = config.getint('CONF', 'screenshot_offset_x')
    y1 = config.getint('CONF', 'screenshot_offset_y')
    x2 = config.getint('CONF', 'screenshot_width')
    y2 = config.getint('CONF', 'screenshot_height')
    w, h = x2-x1, y2-y1
    size = w * h
    objlength = size * 3

    grab.getScreen.argtypes = []
    result = (ctypes.c_ubyte*objlength)()

    grab.getScreen(x1,y1, w, h, result)
    pil = Image.frombuffer('RGB', (w, h), result, 'raw', 'RGB', 0, 1)
    #draw = ImageDraw.Draw(pil)
    #draw.text((100,100), 'abcde')
    return pygame.image.fromstring(pil.tobytes(), pil.size, pil.mode)

def update_workspace(workspace):
    if workspace.num not in global_knowledge.keys():
        global_knowledge[workspace.num] = {
                'name': None,
                'screenshot': None,
                'windows': {}
        }

    global_knowledge[workspace.num]['name'] = workspace.name

    global_knowledge['active'] = workspace.num

def init_knowledge():
    root = i3.get_tree()
    for workspace in root.workspaces():
        update_workspace(workspace)

last_update = 0

def update_state(i3, e):
    global last_update

    if not global_updates_running:
        return False
    if time.time() - last_update < 0.1:
        return False
    last_update = time.time()

    root = i3.get_tree()
    deleted = []
    for num in global_knowledge.keys():
        if type(num) is int and num not in [w.num for w in root.workspaces()]:
            deleted += [num]
    for num in deleted:
        del(global_knowledge[num])

    current_workspace = root.find_focused().workspace()
    update_workspace(current_workspace)

    screenshot = grab_screen()

    #time.sleep(0.5)

    if current_workspace.num == i3ipc.Connection().get_tree().find_focused().workspace().num:
        global_knowledge[current_workspace.num]['screenshot'] = screenshot


def get_hovered_frame(mpos, frames):
    for frame in frames.keys():
        if mpos[0] > frames[frame]['ul'][0] \
                and mpos[0] < frames[frame]['br'][0] \
                and mpos[1] > frames[frame]['ul'][1] \
                and mpos[1] < frames[frame]['br'][1]:
            return frame
    return None

def show_ui():
    global global_updates_running

    window_width = config.getint('CONF', 'window_width')
    window_height = config.getint('CONF', 'window_height')
    
    workspaces = config.getint('CONF', 'workspaces')
    grid_x = config.getint('CONF', 'grid_x')
    grid_y = config.getint('CONF', 'grid_y')
    
    padding_x = config.getint('CONF', 'padding_percent_x')
    padding_y = config.getint('CONF', 'padding_percent_y')
    spacing_x = config.getint('CONF', 'spacing_percent_x')
    spacing_y = config.getint('CONF', 'spacing_percent_y')
    frame_width = config.getint('CONF', 'frame_width_px')
    
    frame_active_color = config.getcolor('CONF', 'frame_active_color')
    frame_inactive_color = config.getcolor('CONF', 'frame_inactive_color')
    frame_unknown_color = config.getcolor('CONF', 'frame_unknown_color')
    frame_empty_color = config.getcolor('CONF', 'frame_empty_color')
    frame_nonexistant_color = config.getcolor('CONF', 'frame_nonexistant_color')
    
    tile_active_color = config.getcolor('CONF', 'tile_active_color')
    tile_inactive_color = config.getcolor('CONF', 'tile_inactive_color')
    tile_unknown_color = config.getcolor('CONF', 'tile_unknown_color')
    tile_empty_color = config.getcolor('CONF', 'tile_empty_color')
    tile_nonexistant_color = config.getcolor('CONF', 'tile_nonexistant_color')
    
    names_show = config.getboolean('CONF', 'names_show')
    names_font = config.get('CONF', 'names_font')
    names_fontsize = config.getint('CONF', 'names_fontsize')
    names_color = config.getcolor('CONF', 'names_color')

    thumb_stretch = config.getboolean('CONF', 'thumb_stretch')
    highlight_percentage = config.getint('CONF', 'highlight_percentage')

    switch_to_empty_workspaces = config.getboolean('CONF', 'switch_to_empty_workspaces')

    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode((window_width, window_height), pygame.RESIZABLE)
    pygame.display.set_caption('i3expo')

    total_x = screen.get_width()
    total_y = screen.get_height()

    pad_x = round(total_x * padding_x / 100)
    pad_y = round(total_y * padding_y / 100)

    space_x = round(total_x * spacing_x / 100)
    space_y = round(total_y * spacing_y / 100)

    shot_outer_x = round((total_x - 2 * pad_x - space_x * (grid_x - 1)) / grid_x)
    shot_outer_y = round((total_y - 2 * pad_y - space_y * (grid_y - 1)) / grid_y)

    shot_inner_x = shot_outer_x - 2 * frame_width 
    shot_inner_y = shot_outer_y - 2 * frame_width

    offset_delta_x = shot_outer_x + space_x
    offset_delta_y = shot_outer_y + space_y

    screen.fill(config.getcolor('CONF', 'bgcolor'))
    
    missing = pygame.Surface((150,200), pygame.SRCALPHA, 32) 
    missing = missing.convert_alpha()
    qm = pygame.font.SysFont('sans-serif', 150).render('?', True, (150, 150, 150))
    qm_size = qm.get_rect().size
    origin_x = round((150 - qm_size[0])/2)
    origin_y = round((200 - qm_size[1])/2)
    missing.blit(qm, (origin_x, origin_y))

    frames = {}

    font = pygame.font.SysFont(names_font, names_fontsize)

    for y in range(grid_y):
        for x in range(grid_x):

            index = y * grid_x + x + 1

            frames[index] = {
                    'active': False,
                    'mouseoff': None,
                    'mouseon': None,
                    'ul': (None, None),
                    'br': (None, None)
            }

            if global_knowledge['active'] == index:
                tile_color = tile_active_color
                frame_color = frame_active_color
                image = global_knowledge[index]['screenshot']
            elif index in global_knowledge.keys() and global_knowledge[index]['screenshot']:
                tile_color = tile_inactive_color
                frame_color = frame_inactive_color
                image = global_knowledge[index]['screenshot']
            elif index in global_knowledge.keys():
                tile_color = tile_unknown_color
                frame_color = frame_unknown_color
                image = missing
            elif index <= workspaces:
                tile_color = tile_empty_color
                frame_color = frame_empty_color
                image = None
            else:
                tile_color = tile_nonexistant_color
                frame_color = frame_nonexistant_color
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
                        origin_x + frame_width,
                        origin_y + frame_width,
                        shot_inner_x,
                        shot_inner_y,
                    ))

            if image:
                if thumb_stretch:
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
                screen.blit(image, (origin_x + frame_width + offset_x, origin_y + frame_width + offset_y))

            mouseoff = screen.subsurface((origin_x, origin_y, shot_outer_x, shot_outer_y)).copy()
            lightmask = pygame.Surface((shot_outer_x, shot_outer_y), pygame.SRCALPHA, 32)
            lightmask.convert_alpha()
            lightmask.fill((255,255,255,255 * highlight_percentage / 100))
            mouseon = mouseoff.copy()
            mouseon.blit(lightmask, (0, 0))

            frames[index]['mouseon'] = mouseon.copy()
            frames[index]['mouseoff'] = mouseoff.copy()

            defined_name = False
            try:
                defined_name = config.get('CONF', 'workspace_' + str(index))
            except:
                pass

            if names_show and (index in global_knowledge.keys() or defined_name):
                if not defined_name:
                    name = global_knowledge[index]['name']
                else:
                    name = defined_name
                name = font.render(name, True, names_color)
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
            active_frame = get_hovered_frame(mpos, frames)
        elif kbdmove != (0, 0):
            if active_frame == None:
                active_frame = 1
            if kbdmove[0] != 0:
                active_frame += kbdmove[0]
            elif kbdmove[1] != 0:
                active_frame += kbdmove[1] * grid_x
            if active_frame > workspaces:
                active_frame -= workspaces
            elif active_frame < 0:
                active_frame += workspaces
            print(active_frame)

        if jump:
            if active_frame in global_knowledge.keys():
                i3.command('workspace ' + str(global_knowledge[active_frame]['name']))
                break
            if switch_to_empty_workspaces:
                defined_name = False
                try:
                    defined_name = config.get('CONF', 'workspace_' + str(active_frame))
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

    converters = {'color': get_color}
    config = configparser.ConfigParser(converters = converters)

    read_config()
    init_knowledge()
    update_state(i3, None)

    i3.on('window::new', update_state)
    i3.on('window::close', update_state)
    i3.on('window::move', update_state)
    i3.on('window::floating', update_state)
    i3.on('window::fullscreen_mode', update_state)
    #i3.on('workspace', update_state)

    i3_thread = Thread(target = i3.main)
    i3_thread.daemon = True
    i3_thread.start()

    while True:
        time.sleep(1)
        update_state(i3, None)
