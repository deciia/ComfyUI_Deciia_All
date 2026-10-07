"""Qwen-Image 2.1 域：入口聚合。"""
from .deciia_preview_save import NODE_CLASS_MAPPINGS as _SAVE_CLASS
from .deciia_preview_save import NODE_DISPLAY_NAME_MAPPINGS as _SAVE_DISPLAY

NODE_CLASS_MAPPINGS = {**_SAVE_CLASS}
NODE_DISPLAY_NAME_MAPPINGS = {**_SAVE_DISPLAY}
