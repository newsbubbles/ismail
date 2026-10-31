"""Callouts and panels over the visual page: their checking, the callout queue, the data panels, the routes, the
ops through a sandbox server, and the page drawing them. No venue is ever streamed to."""
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from ismail.api import OpError
from ismail.stream import config as C, ops, overlay as OV, request_queue as R, server


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(C, 'HOME', tmp_path / 'stream')
    monkeypatch.setenv('ISMAIL_LIVE_REGISTRY', str(tmp_path / 'no_engines'))
    return tmp_path / 'stream'


@pytest.fixture
def sandbox(home):
    """A stream server on a spare port with its own home, which the ops reach through its server.json."""
    hub = server.Hub(port=0)
    server.Handler.hub = hub
    srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    srv.daemon_threads = True
    hub.port = srv.server_address[1]
    home.mkdir(parents=True, exist_ok=True)
    (home / 'server.json').write_text(json.dumps({'port': hub.port, 'pid': os.getpid()}), encoding='utf8')
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield hub
    finally:
        hub.shutdown()
        srv.shutdown()


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.1)
    return fn()


def get(hub, path, timeout=5):
    with urllib.request.urlopen(f'http://127.0.0.1:{hub.port}{path}', timeout=timeout) as r:
        return r.read()


# ------------------------------------------------------------------ checking

def test_callout_checks_point_to_what_each_field_takes(home, tmp_path):
    c = OV.check_callout('drop in 8 bars')
    assert c['slot'] == 'lower-third' and c['place']['valign'] == 'bottom' and c['style']['font_css'].startswith(
        'system-ui') and c['style']['align'] == 'left'
    assert OV.check_callout('hi', x=0.2, y=0.3)['slot'] == 'free'
    assert OV.check_callout('hi', anchor='top', y=0.1)['place'] == {'anchor': 'top', 'x': 0.5, 'y': 0.1, 'w': 0.6,
                                                                    'align': 'center', 'valign': 'top'}
    for kw, says in [(dict(text=''), 'the words to show'),
                     (dict(text='x' * 201), 'at most 200 characters'),
                     (dict(text='a\nb\nc\nd'), 'on 3 lines'),
                     (dict(text='hi', anchor='middle'), 'one of top, top-left'),
                     (dict(text='hi', x=0.2), 'both x= and y='),
                     (dict(text='hi', style={'glow': 1}), "unknown \\['glow'\\]; it takes"),
                     (dict(text='hi', style={'color': 'red'}), "color 'red': '#rrggbb'"),
                     (dict(text='hi', style={'background': 'blue'}), "'#rrggbb' or 'none'"),
                     (dict(text='hi', style={'size': 0.9}), 'size: a number from 0.01 to 0.3'),
                     (dict(text='hi', style={'weight': 650}), '100, 200'),
                     (dict(text='hi', style={'font': 'Comic Neue'}), 'a name \\(sans, serif'),
                     (dict(text='hi', enter='zoom'), 'enter: one of none, fade'),
                     (dict(text='hi', duration_s=1, anim_s=0.5), 'at most a third of duration_s'),
                     (dict(text='hi', priority=12), 'priority: a whole number 0-9'),
                     (dict(text='hi', slot='Big Slot'), 'slot')]:
        with pytest.raises(ValueError, match=says):
            OV.check_callout(**kw)
    assert OV.check_callout('hi', style={'font': 'Impact'})['style']['font'] == 'impact'
    outside = tmp_path / 'elsewhere.ttf'
    outside.write_bytes(b'\0' * 64)
    with pytest.raises(ValueError, match='must sit under'):
        OV.check_callout('hi', style={'font': str(outside)})
    font = home / 'fonts' / 'mine.ttf'
    font.parent.mkdir(parents=True)
    font.write_bytes(b'\0' * 64)
    s = OV.check_callout('hi', style={'font': str(font)})['style']
    fid = s['font_url'].rsplit('/', 1)[-1]
    assert s['font_css'].startswith(f'"ismail-{fid}"') and OV.FONT_FILES[fid] == font.resolve()


# ------------------------------------------------------------------ the queue

def test_callouts_take_turns_in_a_slot_by_priority_back_to_back():
    q, t = OV.Callouts(), 1000.0
    a = q.add(OV.check_callout('one', duration_s=4), t)
    b = q.add(OV.check_callout('two', duration_s=4), t + 1)
    c = q.add(OV.check_callout('urgent', duration_s=3, priority=9), t + 2)
    top = q.add(OV.check_callout('elsewhere', anchor='top', duration_s=5), t + 2)
    s = {d['id']: d for d in q.schedule(t + 2)}
    assert s[a['id']]['state'] == 'showing' and s[top['id']]['state'] == 'showing'      # two slots at once
    assert s[c['id']]['start'] == t + 4 and s[b['id']]['start'] == t + 7               # priority first, no gap
    with pytest.raises(ValueError, match='says that in lower-third already'):
        q.add(OV.check_callout('two'), t + 2)
    # time moves on: 'one' ends at t+4 and 'urgent' starts exactly then, though nobody asked until t+5
    s = {d['id']: d for d in q.schedule(t + 5)}
    assert a['id'] not in s and s[c['id']]['state'] == 'showing' and s[c['id']]['start'] == t + 4
    assert q.schedule(t + 30) == []                                                      # all expired
    q.add(OV.check_callout("late"), t + 31)
    assert q.schedule(t + 31)[0]['start'] == t + 31                                      # a free slot: at once


def test_callouts_clear_by_id_slot_or_all():
    q, t = OV.Callouts(), 50.0
    ids = [q.add(OV.check_callout(f'n{i}', anchor=a), t)['id'] for i, a in enumerate(['top', 'top', 'bottom'])]
    assert q.clear(t, id=ids[0]) == [ids[0]]
    assert q.schedule(t)[1]['id'] == ids[1] and q.schedule(t)[1]['state'] == 'showing'  # the next moves up
    with pytest.raises(ValueError, match='no callout #99'):
        q.clear(t, id=99)
    assert q.clear(t, slot='bottom') == [ids[2]] and q.clear(t) == [ids[1]] and q.schedule(t) == []
    for i in range(OV.MAX_QUEUE + 1):
        q.add(OV.check_callout(f'q{i}'), t)
    with pytest.raises(ValueError, match='wait there already'):
        q.add(OV.check_callout('one too many'), t)


# ------------------------------------------------------------------ panels and their data

def test_panels_show_the_queue_chat_and_credits(sandbox, home):
    hub = sandbox
    R.request_add(who='ViewerA', what='deep house', venue='practice')
    R.request_add(who='ViewerB', what='a sad cello over rain', venue='practice')
    R.request_add(who='ViewerC', what='jungle')
    hub.log.add('chat', 'practice', platform='fake', user={'name': 'viewera', 'display': 'ViewerA'}, text='hello!')
    hub.log.add('chat', 'practice', platform='fake', user={'name': 'me'}, text='our echo', own=True)
    hub.log.add('event', 'tw', platform='twitch', user={'display': 'Raider'}, type='raid', amount=12)
    hub.op_panel('requests', rows=2)
    hub.op_panel('chat', venues=None)
    hub.op_panel('now', empty='')
    hub.op_panel('credits', empty='your name here')
    shows = {k: p['shows'] for k, p in hub.op_panel()['panels'].items()}
    assert [(r['place'], r['text'], r['by']) for r in shows['requests']] == [(1, 'deep house', 'ViewerA'),
                                                                               (2, 'a sad cello over rain', 'ViewerB')]
    assert [(r['by'], r['text']) for r in shows['chat']] == [('ViewerA', 'hello!'), ('Raider', 'raid 12')]
    assert shows['now'] == [] and shows['credits'] == []
    hub.op_text('now', 'request from @ViewerZ: now playing an older one')    # request_mark's line, now stale
    assert {p['kind']: p['shows'] for p in hub.live()['panels']}['now'] == []
    R.request_mark(id=1, status='playing', on_screen=False)
    shows = {p['kind']: p['shows'] for p in hub.live()['panels']}
    assert shows['now'] == [{'text': 'deep house', 'by': 'ViewerA', 'venue': 'practice'}]
    assert shows['credits'][0]['by'] == 'ViewerA' and shows['requests'][0]['text'] == 'a sad cello over rain'
    hub.op_text('now', 'a set of my own')            # with no request playing, the stream's now line
    R.request_mark(id=1, status='done')
    assert {p['kind']: p['shows'] for p in hub.live()['panels']}['now'] == [{'text': 'a set of my own'}]
    # fields left out keep their value; a style merges key by key; it all survives a restart
    hub.op_panel('requests', style={'color': '#ffee00'})
    hub.op_panel('requests', style={'size': 0.04}, anchor='left')
    p = hub.panels['requests']
    assert p['rows'] == 2 and p['style']['color'] == '#ffee00' and p['style']['size'] == 0.04
    again = server.Hub(port=0)
    assert again.panels['requests']['place']['anchor'] == 'left' and again.panels['requests']['rows'] == 2
    with pytest.raises(ValueError, match='rows: a whole number 1-12'):
        hub.op_panel('chat', rows=40)
    with pytest.raises(ValueError, match="unknown \\['colour'\\]"):
        hub.op_panel('chat', colour='#ffffff')
    with pytest.raises(ValueError, match='one of now, requests, chat, credits'):
        hub.op_panel('weather')
    hub.op_panel('chat', on=False)
    assert 'chat' not in hub.panels and 'chat' not in json.loads((home / 'panels.json').read_text())
    with pytest.raises(ValueError, match='no chat panel is up'):
        hub.op_panel('chat', on=False)


# ------------------------------------------------------------------ routes and ops

def test_the_live_route_wakes_on_a_change_and_serves_fonts(sandbox, home):
    hub = sandbox
    v = json.loads(get(hub, '/visual/live?v=0'))['v']
    t0 = time.time()
    quiet = json.loads(get(hub, f'/visual/live?v={v}&wait=0.5'))
    assert quiet['callouts'] == [] and 0.4 < time.time() - t0 < 3                       # nothing new: about a beat
    got = {}
    th = threading.Thread(target=lambda: got.update(json.loads(get(hub, f'/visual/live?v={v}&wait=5', 10))))
    t0 = time.time()
    th.start()
    time.sleep(0.2)
    hub.op_callout(text='hello room', anchor='top', who='MC')
    th.join(5)
    assert time.time() - t0 < 2 and got['v'] == v + 1 and got['callouts'][0]['text'] == 'hello room'
    assert got['callouts'][0]['place']['anchor'] == 'top' and abs(got['t'] - time.time()) < 2
    logged = json.loads((home / 'callouts.jsonl').read_text().splitlines()[-1])
    assert logged['who'] == 'MC' and logged['text'] == 'hello room'
    font = home / 'fonts' / 'mine.woff2'
    font.parent.mkdir(parents=True)
    font.write_bytes(b'wOF2' + b'\0' * 60)
    hub.op_callout(text='in my font', style={'font': str(font)})
    url = next(c for c in hub.live()['callouts'] if c['text'] == 'in my font')['style']['font_url']
    assert get(hub, url).startswith(b'wOF2')
    with pytest.raises(urllib.error.HTTPError):
        get(hub, '/visual/font/0123456789ab')


def test_the_ops_say_what_shows_and_what_waits(sandbox):
    out = ops.stream_callout(text='drop in 8 bars', duration_s=8, who='DJ')
    assert '#1 in lower-third: showing now for 8.0 s' in out and 'queued: none' in out
    out = ops.stream_callout(text='then the break', duration_s=4)
    assert '#2 in lower-third: queued, starts in 8.0 s' in out or 'starts in 7.9 s' in out
    assert "showing: #1 'drop in 8 bars'" in out and 'stream_callout(clear=True, id=<n>)' in out
    with pytest.raises(OpError, match='at most a third'):
        ops.stream_callout(text='blink', duration_s=1, anim_s=1)
    with pytest.raises(OpError, match='text= to show one'):
        ops.stream_callout(id=2)
    assert 'took down #1' in ops.stream_callout(clear=True, id='#1')
    assert "showing: #2 'then the break'" in ops.stream_callout()
    assert 'took down #2' in ops.stream_callout(clear=True) and 'showing: nothing' in ops.stream_callout()
    with pytest.raises(OpError, match="no callout in slot 'top'"):
        ops.stream_callout(clear=True, slot='top')
    out = ops.stream_panel('requests', empty='type !request <style> in chat', anchor='right')
    assert "requests: right, 5 rows, title 'Up next'; shows nothing yet, so it shows 'type !request" in out
    R.request_add(who='ViewerA', what='deep house')
    assert 'shows 1 deep house @ViewerA' in ops.stream_panel()
    assert 'took down the requests panel' in ops.stream_panel('requests', on=False)


def test_private_words_never_reach_the_page(sandbox, tmp_path, monkeypatch):
    terms = tmp_path / 'private_terms.txt'
    terms.write_text('Placeville\n', encoding='utf8')
    monkeypatch.setenv('ISMAIL_PRIVATE_TERMS', str(terms))
    with pytest.raises(OpError, match='personal detail'):
        ops.stream_callout(text='live from Placeville tonight')
    with pytest.raises(OpError, match='personal detail'):
        ops.stream_panel('chat', title='Chat in Placeville')
    assert sandbox.callouts.items == [] and 'chat' not in sandbox.panels


# ------------------------------------------------------------------ the page draws them

def _browser():
    for p in (shutil.which('msedge'), shutil.which('chrome'), shutil.which('chromium'),
              r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
              r'C:\Program Files\Google\Chrome\Application\chrome.exe'):
        if p and Path(p).exists():
            return p
    return None


@pytest.mark.skipif(not _browser(), reason='needs Edge or Chrome for a headless look')
def test_the_page_draws_a_callout_and_a_panel(sandbox, tmp_path):
    hub = sandbox
    hub.op_callout(text='drop in 8 bars', anchor='center', duration_s=60, enter='pop',
                   style={'background': '#ff2266', 'size': 0.08})
    R.request_add(who='ViewerA', what='deep house')
    hub.op_panel('requests')
    proc = subprocess.Popen([_browser(), '--headless=new', '--disable-gpu', '--no-first-run', '--mute-audio',
                             f'--user-data-dir={tmp_path / "profile"}', '--window-size=1280,720',
                             f'http://127.0.0.1:{hub.port}/visual?debug=1'],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        drew = wait_for(lambda: (hub.page_drew or {}).get('callouts') and hub.page_drew.get('panels')
                        and hub.page_drew, timeout=30)
        assert drew and drew['callouts'] == [1] and drew['panels'] == ['requests']
        assert 'the page draws: callouts #1; panels requests' in ops.stream_page()
        hub.op_callout(clear=True)
        hub.op_panel('requests', on=False)
        assert wait_for(lambda: hub.page_drew['callouts'] == [] and hub.page_drew['panels'] == [], timeout=10), (hub.page_drew, time.time())
    finally:
        proc.terminate()
        proc.wait(10)
