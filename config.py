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
        'tile_active_color':           '#5a6da4',
        'tile_inactive_color':         '#93afb3',
        'tile_unknown_color':          '#ffe6d0',
        'tile_empty_color':            'gray80',
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


class Config(SimpleNamespace):
    def read_config(self):
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

        value_order = [config.getfloat, config.getint, config.getcolor, config.getboolean, config.get]

        for group in ['Capture', 'UI', 'Fonts', 'Flags', 'Colors']:
            for item in config[group]:
                for func in value_order:
                    try:
                        setattr(self, item, func(group, item))
                        break
                    except ValueError:
                        pass
                if item not in dir(self):
                    raise ValueError("Invalid config value for " + item)

        setattr(self, 'workspace_names', {})
        for item in config['Workspaces']:
            if item[:10] == 'workspace_':
                self.workspace_names[int(item[10:])] = config.get('Workspaces', item)
            else:
                raise ValueError("Invalid config variable: " + item)

        self.calculate()

    def calculate(self):
        self.window_dim = [self.window_width, self.window_height]
        self.screenshot_dim = [self.screenshot_width, self.screenshot_height]
        self.screenshot_offset = [self.screenshot_offset_x, self.screenshot_offset_y]
        self.grid = [self.grid_x, self.grid_y]
        padding_pct = [self.padding_percent_x, self.padding_percent_y]
        spacing_pct = [self.spacing_percent_x, self.spacing_percent_y]
        self.padding = [self.window_dim[n] * padding_pct[n] / 100 for n in (0,1)]
        self.spacing = [self.window_dim[n] * spacing_pct[n] / 100 for n in (0,1)]
        self.tile_dim_outer = [round((self.window_dim[n] - 2 * self.padding[n] -
                                       self.spacing[n] * (self.grid[n] - 1)) /
                                     self.grid[n]) for n in (0,1)]
        self.tile_dim_inner = [self.tile_dim_outer[n] - 2 * self.frame_width_px for n in (0, 1)]
        self.colors = {
                'active': { 'frame': self.frame_active_color, 'tile': self.tile_active_color },
                'inactive': { 'frame': self.frame_inactive_color, 'tile': self.tile_inactive_color },
                'unknown': { 'frame': self.frame_unknown_color, 'tile': self.tile_unknown_color },
                'empty': { 'frame': self.frame_empty_color, 'tile': self.tile_empty_color }
        }


CONF = Config()
CONF.read_config()
