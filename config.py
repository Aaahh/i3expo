#!/usr/bin/python3

import os
import configparser
import logging
from types import SimpleNamespace

import pygame
from xdg.BaseDirectory import xdg_config_home

CONFIG_FILE = os.path.join(xdg_config_home, 'i3expo', 'config')

log = logging.getLogger('conf')

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
