import os
import shutil

import folder_paths

CACHE_SUBFOLDER = "AutoCachedPreview"
WEB_DIRECTORY = "./js"


def _clear_cache_on_startup():
    cache_dir = os.path.join(folder_paths.get_temp_directory(), CACHE_SUBFOLDER)
    if os.path.isdir(cache_dir):
        shutil.rmtree(cache_dir, ignore_errors=True)
    os.makedirs(cache_dir, exist_ok=True)


_clear_cache_on_startup()

from .auto_cached_preview import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
