"""Who is in the headset, and who is listening to them.

2026-10-04: the user spent nine minutes in VR and sent six voice notes, and no agent heard one ("Is there a reason
you're not using voice to talk to me?"). The live link is pull only: an agent hears the user only while it polls.
So the server keeps presence:

- a listener is anything following the live log: GET /live/events with since= (that scene), or GET /live/inbox
  (every scene). `who=` names it; `who=op` (an op waiting for its own answer) is not a listener.
- a voice note no listener took within HEARD_WAIT_S is unheard: the headset says so out loud, the note is kept in
  <state>/unread.jsonl, and the voice_unheard hooks run. A note a listener took but did not answer is said to be
  "handed to <who>" (not the listener's own ack: the user can tell taken from answered).
- hooks: commands in ~/.ismail/stage_hooks.json, run on entered_vr, left_vr, voice_note and voice_unheard (any
  agent: a toast, a webhook, `claude -p`, `codex exec`), with STAGE_* in their environment. Never from the scenes
  folder by itself: a song's folder travels (a copied world), so its <state>/hooks.json runs only when the home file
  names that scenes folder in "trust".
- GET /live/presence and <state>/presence.json: in VR or not, the scene, the last note, the listeners, the unread.
"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

LISTEN_FRESH_S = 40          # a listener is gone when its last poll is older (long polls wait up to 25 to 60 s)
HEARD_WAIT_S = 6             # a note no listener took by then is unheard
PAGE_FRESH_S = 120           # the headset page posts state about once a minute while it is lifted
NOBODY = 'Nobody is listening right now. I saved your note.'
HOOK_EVENTS = ('entered_vr', 'left_vr', 'voice_note', 'voice_unheard')
# what stage_listen hears: the person's voice and headset, scene switches, and what an agent must answer for the things
# it wrote (a behaviour that failed, a message an object sent, a page error; until 2026-10-12 only stage_events showed them)
INBOX_TYPES = {'voice_in', 'voice_message', 'voice_heard', 'headset', 'vr_exit', 'scene_switched', 'perform_clip',
               'behaviour_error', 'object_message', 'page_error'}
ANSWERS = {'ack', 'say', 'ask'}

LOCK = threading.Lock()
P = {'in_vr': False, 'scene': None, 'since': None, 'page_seen': 0.0, 'last_voice': None, 'unread': 0}
LISTENERS = {}               # key -> {who, scene ('*' = every scene), seen, delivered: {scene: event id}, inbox: seq}
ANSWERED = {}                # scene -> time of the last ack / say / ask queued for it
NOTES = {}                   # (scene, file) -> {id, t, seq, state: 'waiting' | 'heard' | 'handed' | 'unheard', text}
INBOX = []                   # every scene's voice and headset events, numbered across scenes: {seq, scene, ...}
SEQ = [0]
STARTED = time.time()


def reset():
    """A fresh server (configure): nobody in VR, nobody listening, no notes."""
    with LOCK:
        P.update({'in_vr': False, 'scene': None, 'since': None, 'page_seen': 0.0, 'last_voice': None, 'unread': 0})
        LISTENERS.clear(), ANSWERED.clear(), NOTES.clear(), INBOX.clear()
        SEQ[0] = 0


def _server():
    from . import server
    return server


def listener_key(who, scene):
    return f'{who}' if who else f'anon/{scene}'


def seen(who, scene, delivered=None, inbox=None):
    """A listener polled: who (None = unnamed), the scene it follows ('*' = every scene through the inbox), and the
    newest event id (or inbox seq) it has been handed."""
    if who == 'op':
        return
    k = listener_key(who, scene)
    with LOCK:
        L = LISTENERS.setdefault(k, {'who': who or 'unnamed', 'scene': scene, 'delivered': {}, 'inbox': 0, 'since': time.time()})
        L['seen'], L['scene'] = time.time(), scene
        if delivered is not None:
            L['delivered'][scene] = max(L['delivered'].get(scene, 0), delivered)
        if inbox is not None:
            L['inbox'] = max(L['inbox'], inbox)


def listening(scene=None):
    """The listeners polling now (for a scene: those following it or the inbox)."""
    now = time.time()
    with LOCK:
        return sorted({L['who'] for L in LISTENERS.values() if now - L['seen'] < LISTEN_FRESH_S
                       and (scene is None or L['scene'] in (scene, '*'))})


def _took(scene, note):
    """Who has been handed this note (its event id on the scene, or its seq in the inbox)."""
    with LOCK:
        return sorted({L['who'] for L in LISTENERS.values()
                       if L['delivered'].get(scene, 0) >= note['id'] or (L['scene'] == '*' and L['inbox'] >= note['seq'])})


def inbox_add(scene, e):
    S = _server()
    with S.COND:
        SEQ[0] += 1
        x = {'seq': SEQ[0], 'scene': scene, **{k: v for k, v in e.items() if k not in ('snap', 'snaps', 'during')}}
        INBOX.append(x)
        del INBOX[:-500]
        S.COND.notify_all()
    return x


def inbox_after(since, limit=200):
    return [e for e in INBOX if e['seq'] > since][-limit:]


def page_alive(scene):
    with LOCK:
        P['page_seen'] = time.time()


def on_events(scene, evs):
    """Events the page posted or the server added: track the headset and the voice notes."""
    for e in evs:
        t = e.get('type')
        if e.get('page'):                          # posted by a page (the server's own events carry none)
            page_alive(scene)
        if t == 'perform_clip':                    # what they say inside a performance: the text first, no word list
            x = inbox_add(scene, {'type': t, 'text': e.get('text'), **{k: v for k, v in e.items() if k not in ('words', 'text', 'type')}}) \
                if (e.get('text') or '').strip() else None
            continue
        x = inbox_add(scene, e) if t in INBOX_TYPES else None
        if t == 'headset' and e.get('state') in ('entered VR', 'on'):
            with LOCK:
                was, P['in_vr'], P['scene'] = P['in_vr'], True, scene
                if not was:
                    P['since'] = time.time()
            if not was:
                _changed()
                fire('entered_vr', scene, event_id=e.get('id'))
        elif (t == 'headset' and e.get('state') == 'off') or t == 'vr_exit':
            with LOCK:
                was, P['in_vr'] = P['in_vr'], False
            if was:
                _changed()
                fire('left_vr', scene, event_id=e.get('id'))
        elif t == 'scene_switched':
            with LOCK:
                P['scene'] = e.get('to') or scene
        elif t == 'voice_in' and float(e.get('seconds') or 0) >= 1.5:
            with LOCK:
                NOTES[(scene, e.get('file'))] = {'id': e['id'], 'seq': x['seq'], 't': time.time(), 'state': 'waiting', 'text': None}
                P['scene'] = scene
            threading.Timer(HEARD_WAIT_S, _check, (scene, e.get('file'))).start()
        elif t == 'voice_message':
            with LOCK:
                n = NOTES.get((scene, e.get('file')))
                if n:
                    n['text'] = e.get('text')
                P['last_voice'] = {'scene': scene, 'id': e.get('id'), 'ts': e.get('ts'), 'text': (e.get('text') or '')[:300],
                                   'heard': n['state'] if n else None}
            _changed()
            fire('voice_note', scene, event_id=e.get('id'), text=e.get('text'), file=e.get('file'))
            if n and n['state'] == 'unheard':
                fire('voice_unheard', scene, event_id=e.get('id'), text=e.get('text'), file=e.get('file'))


def on_cmds(scene, cmds):
    if any(c.get('type') in ANSWERS for c in cmds):
        with LOCK:
            ANSWERED[scene] = time.time()


def _check(scene, file):
    """HEARD_WAIT_S after a note landed: answered, handed to a listener (ack it for them), or nobody (say so)."""
    S = _server()
    with LOCK:
        n = NOTES.get((scene, file))
        if not n or n['state'] != 'waiting':
            return
        answered = ANSWERED.get(scene, 0) >= n['t']
    took = _took(scene, n)
    if answered:
        state = 'heard'
    elif took:
        state = 'handed'
        names = [w for w in took if w != 'unnamed'] or ['an agent']
        S.server_cmd(scene, {'type': 'say', 'text': 'Handed to ' + ' and '.join(names) + '.', 'by': 'server: handed', 'from': 'stage'})
    else:
        state = 'unheard'
        S.server_cmd(scene, {'type': 'say', 'text': NOBODY, 'by': 'server: no listener', 'from': 'stage'})
        S.append_lines(S.STATE / 'unread.jsonl', [{'ts': S.now_iso(), 'scene': scene, 'event_id': n['id'], 'file': file}])
    with LOCK:
        n['state'] = state
        if state == 'unheard':
            P['unread'] += 1
        if P['last_voice'] and P['last_voice'].get('id') and P['last_voice']['scene'] == scene:
            P['last_voice']['heard'] = state
        text = n['text']
    S.server_event(scene, {'type': 'voice_heard', 'file': file, 'note_id': n['id'], 'state': state, 'listeners': took})
    _changed()
    if state == 'unheard' and text is not None:     # transcribed already: the hooks get the text now
        fire('voice_unheard', scene, event_id=n['id'], text=text, file=file)


def snapshot():
    now = time.time()
    with LOCK:
        ls = [{'who': L['who'], 'scene': L['scene'], 'age_s': round(now - L['seen'], 1),
               'listening': now - L['seen'] < LISTEN_FRESH_S} for L in LISTENERS.values()]
        p = dict(P)
    fresh = now - p['page_seen'] < PAGE_FRESH_S
    return {'in_vr': p['in_vr'] and fresh, 'scene': p['scene'], 'since': p['since'] and round(now - p['since']),
            'page_age_s': round(now - p['page_seen'], 1) if p['page_seen'] else None, 'last_voice': p['last_voice'],
            'unread': p['unread'], 'listening': sorted({x['who'] for x in ls if x['listening']}),
            'listeners': sorted(ls, key=lambda x: x['age_s']), 'inbox_last': SEQ[0],
            'server': {'pid': os.getpid(), 'started': round(STARTED)},
            'hooks': {k: len(v) for k, v in hooks().items() if v}}


def _changed():
    S = _server()
    S.write_atomic(S.STATE / 'presence.json', json.dumps(snapshot(), indent=1))


# ---- hooks
def home_hooks():
    return Path(os.environ.get('ISMAIL_STAGE_HOOKS') or Path.home() / '.ismail' / 'stage_hooks.json')


def _read(f):
    try:
        d = json.loads(Path(f).read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def hooks():
    """The home file's hooks, plus <state>/hooks.json when the home file trusts this scenes folder:
    {"trust": ["D:/ismail/songs/crossroads/video/vr/scenes"], "voice_unheard": [...]}."""
    S = _server()
    home = _read(home_hooks())
    trusted = {str(Path(p).resolve()).lower() for p in home.get('trust') or []}
    files = [home] + ([_read(S.STATE / 'hooks.json')] if str(S.SCENES.resolve()).lower() in trusted else [])
    out = {k: [] for k in HOOK_EVENTS}
    for d in files:
        for k in HOOK_EVENTS:
            v = d.get(k) or []
            out[k] += v if isinstance(v, list) else [v]
    return out


URL = ['http://127.0.0.1:8862']


def fire(event, scene, event_id=None, text=None, file=None):
    """Run the hooks for an event, each in its own process (never waited for); output goes to <state>/hooks.log."""
    cmds = hooks().get(event) or []
    if not cmds:
        return
    S = _server()
    env = {**os.environ, 'STAGE_EVENT': event, 'STAGE_SCENE': scene or '', 'STAGE_URL': URL[0],
           'STAGE_EVENT_ID': str(event_id or ''), 'STAGE_TEXT': text or '', 'STAGE_FILE': file or '',
           'STAGE_INBOX_SEQ': str(SEQ[0]), 'STAGE_SCENES': str(S.SCENES), 'STAGE_STATE': str(S.STATE)}
    for c in cmds:
        try:
            log = open(S.STATE / 'hooks.log', 'a', encoding='utf-8')
            log.write(f'{S.now_iso()} {event} {scene}: {c}\n')
            log.flush()
            subprocess.Popen(c, shell=isinstance(c, str), env=env, cwd=str(S.STATE), stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            print(f'[hook] {event} {scene}: {c}', flush=True)
        except (OSError, ValueError) as x:
            print(f'[hook] {event} failed: {c}: {x}', flush=True)
