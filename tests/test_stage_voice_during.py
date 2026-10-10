"""A voice note carries what the user did while it recorded (during_note): selections, the menus they opened, the
buttons they pressed and why a panel closed, so an agent reads "this one" without digging through the event log."""
import time

from ismail.stage import server as S


def test_a_note_lists_menus_and_presses_made_while_it_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_STAGE_REGISTRY', str(tmp_path / 'reg'))
    (tmp_path / 'scenes' / 'room').mkdir(parents=True)
    S.LIVE.clear()
    S.configure(tmp_path / 'scenes', state=str(tmp_path / 'state'))
    for ev in ({'type': 'select', 'item': 'stool_a', 'hand': 'right'},
               {'type': 'panel_shown', 'id': 'menu_1', 'title': 'stool_a'},
               {'type': 'panel_answer', 'id': 'menu_1', 'answer': 'Move'},
               {'type': 'behaviour_run', 'object': 'switch_a', 'action': 'press'},
               {'type': 'panel_closed', 'id': 'menu_2', 'why': 'timeout'},
               {'type': 'frame_beat', 'fps': 40}):                  # not an interaction: left out
        S.server_event('room', ev)
    lines = S.during_note('room', 5, time.time())
    text = ' | '.join(lines)
    assert 'select right stool_a' in text
    assert 'panel_shown stool_a' in text and 'panel_answer menu_1 -> Move' in text
    assert 'behaviour_run switch_a press' in text and 'panel_closed menu_2 timeout' in text
    assert 'frame_beat' not in text
