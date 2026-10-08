"""A command with NaN or Infinity never reaches the page: the page cannot parse it, and its command queue would stall
(2026-10-08: a light set from a 0 W start answered null and the room went black)."""
import json

import pytest

from ismail import api  # noqa: F401  (registers the ops before link is imported)
from ismail.api import OpError
from ismail.stage import link, server


def test_the_agent_link_refuses_a_number_that_is_not_finite():
    for bad in (float('nan'), float('inf'), -float('inf')):
        with pytest.raises(OpError, match='not finite'):
            link.http({'port': 1}, 'live/cmd', {'type': 'light', 'light': 'x', 'energy': bad})


def test_the_server_refuses_nan_and_infinity_in_a_body():
    for word in ('NaN', 'Infinity', '-Infinity'):
        with pytest.raises(ValueError):
            json.loads('{"energy": %s}' % word, parse_constant=server._not_finite)
    assert json.loads('{"energy": 300.0}', parse_constant=server._not_finite) == {'energy': 300.0}
