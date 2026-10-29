"""Chat adapters, one per platform. The site ones load on first use (they need the `websockets` package)."""
import importlib

from .base import Adapter, NeedsLogin  # noqa: F401

CLASSES = {'fake': ('fake', 'FakeAdapter'), 'twitch': ('twitch', 'TwitchAdapter'),
           'youtube': ('youtube', 'YouTubeAdapter'), 'kick': ('kick', 'KickAdapter'),
           'owncast': ('owncast', 'OwncastAdapter')}


def make(venue, hub):
    mod, cls = CLASSES[venue['platform']]
    return getattr(importlib.import_module(f'.{mod}', __name__), cls)(venue, hub)
