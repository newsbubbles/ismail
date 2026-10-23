"""The CLI on a console that isn't UTF-8 (Windows' cp1252 when piped)."""
import io
import json

from ismail.__main__ import console_safe


def test_a_character_the_console_cannot_take_comes_out_as_a_json_escape():
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding='cp1252')
    console_safe(out)
    row = {'kind': 'hum', 'note': 'C♯4', 'mood': '\U0001f3b5', 'name': 'café'}
    print(json.dumps(row, ensure_ascii=False), file=out)
    out.flush()
    text = raw.getvalue().decode('ascii')
    assert json.loads(text) == row


def test_a_utf8_console_is_left_alone():
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding='utf-8')
    console_safe(out)
    print('C♯4', file=out)
    out.flush()
    assert raw.getvalue().decode('utf-8').strip() == 'C♯4'


def test_python_spelled_booleans_are_booleans():
    from ismail.__main__ import parse_args
    assert parse_args(['grab=False', 'fixed=True', 'wp=None', 'name=Falsey']) == \
        {'grab': False, 'fixed': True, 'wp': None, 'name': 'Falsey'}
