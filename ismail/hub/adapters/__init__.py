"""Hub adapters, one per platform. The site ones load on first use (they need the `websockets` package)."""
import importlib

from .base import HubAdapter, NeedsLogin  # noqa: F401

CLASSES = {'fake': ('fake', 'FakeAdapter'), 'discord': ('discord', 'DiscordAdapter')}


def make(place, hub):
    mod, cls = CLASSES[place['platform']]
    return getattr(importlib.import_module(f'.{mod}', __name__), cls)(place, hub)
