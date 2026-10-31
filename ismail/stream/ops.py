"""stream_* / venue_* / chat_* ops: agents run a stream to several sites and talk with every site's chat.

The stream server holds the chat connections and the output (server.py); these ops are thin calls to it, except
venue_set / venue_list / venue_remove, which edit the venue file and work with the server down too."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from ..api import OpError, op
from .. import guards
from . import config as C


def _port():
    try:
        return int(json.loads((C.HOME / 'server.json').read_text(encoding='utf8'))['port'])
    except (OSError, ValueError, KeyError):
        return None


def _call(name, timeout=30, **args):
    port = _port()
    if port is None:
        raise OpError("the stream server is not running: stream_start() first")
    req = urllib.request.Request(f'http://127.0.0.1:{port}/agent', headers={'Content-Type': 'application/json'},
                                 data=json.dumps({'op': name, 'args': {k: v for k, v in args.items()
                                                                       if v is not None}}).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        res = json.loads(e.read() or b'{}')
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise OpError(f"the stream server does not answer ({e}): stream_start() again (log: "
                      f"{C.HOME / 'server.log'})")
    if not res.get('ok'):
        raise OpError(res.get('error', 'stream server error'))
    return res['result']


def _up(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as r:
            return json.loads(r.read()).get('ok')
    except (OSError, ValueError):
        return False


# ------------------------------------------------------------------ the server

@op()
def stream_start(port: int = C.PORT) -> str:
    """Start the stream server (idempotent). It connects to every venue's chat and waits; it sends nothing to any
    site until stream_go. Then: venue_list to see what is ready, chat_watch to read chat."""
    p = _port()
    if p and _up(p):
        return f"already running on 127.0.0.1:{p}\n" + stream_status()
    C.HOME.mkdir(parents=True, exist_ok=True)
    log = open(C.HOME / 'server.log', 'a', encoding='utf8')
    flags = (0x00000008 | 0x00000200 | 0x08000000) if os.name == 'nt' else 0   # detached, own group, no window
    subprocess.Popen([sys.executable, '-m', 'ismail.stream.server', '--port', str(port)], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, creationflags=flags, start_new_session=os.name != 'nt',
                     cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    for _ in range(50):
        time.sleep(0.2)
        if _up(port):
            break
    else:
        raise OpError(f"the stream server did not come up on {port}: read {C.HOME / 'server.log'}")
    return f"stream server up on 127.0.0.1:{port}\n" + stream_status()


@op()
def stream_stop() -> str:
    """Stop the stream server: ends any output first (every site goes offline), then closes every chat."""
    p = _port()
    if not p or not _up(p):
        return 'the stream server is not running'
    try:
        _call('end', timeout=30)
    except OpError:
        pass
    pid = json.loads((C.HOME / 'server.json').read_text(encoding='utf8')).get('pid')
    try:
        import psutil
        psutil.Process(int(pid)).terminate()
    except Exception as e:
        raise OpError(f"could not stop the stream server (pid {pid}): {e}")
    return 'stream server stopped: no output, no chat'


@op()
def stream_status() -> str:
    """The server, each venue (chat state, whether it streams, which keys are in place, never their values), who is
    watching chat, and the output (live or not, per site, bitrate, dropped frames)."""
    st = _call('status')
    out = [f"stream server: up {st['up_s'] // 60} min, {st['chat_lines']} chat lines, watching chat: "
           f"{', '.join(st['watching']) or 'nobody'}"]
    for v in st['venues']:
        chat = v['chat']
        cs = (chat if isinstance(chat, str) else
              f"chat {chat['state']} ({chat['reading']}, {chat['got']} in, {chat['sent']} out)"
              + (f": {chat['error']}" if chat.get('error') else ''))
        out.append(f"  {v['venue']} ({(v['platform'] + ' ' + (v.get('channel') or '')).strip()}): {cs}; "
                   f"{'streams' if v['output'] else 'no output'}; keys {_keys_line(v['keys'])}")
    o = st['output']
    out.append('output: ' + (o.get('summary') or o.get('state', 'off')))
    return '\n'.join(out)


def _keys_line(k):
    if not k:
        return 'none needed'
    return ', '.join(f"{f} {'yes' if v is True else 'NO' if v is False else v}" for f, v in k.items())


# ------------------------------------------------------------------ venues

@op(mutates=True)
def venue_set(name: str, platform: str, channel: str = None, chat: bool = True, output: bool = None,
              ingest: str = None, script: list = None, loop: bool = None, reader: str = None, poll_s: float = None,
              note: str = None) -> str:
    """Add or change a venue: one channel on one site. No secret ever goes here.
    name: short and lowercase ('yt', 'twitch-main', 'practice'). platform: fake, twitch, youtube, kick, owncast,
    rtmp (output only) or file (a local recording; channel is the .flv path). channel: the channel's login name
    (twitch, kick), handle or channel id (youtube), server address (owncast). chat: read and answer its chat.
    output: send the stream there on stream_go (default: yes where the site takes video). ingest: an RTMP(S) address
    when the default does not fit. script / loop (fake only): a scripted test viewer, [{after_s, user, text} or
    {after_s, user, event}]. reader / poll_s (youtube only): 'stream' (the default: realtime, one call held open) or
    'poll' (a list call every poll_s seconds, 10 by default, at least 5). Returns what the venue still needs from the person (a login, a stream key) and the
    exact line they type in a terminal for it."""
    name = str(name).strip().lower()
    if not C.NAME_RE.match(name):
        raise OpError(f"name {name!r}: 1-32 of a-z, 0-9, - and _, starting with a letter or digit")
    if platform not in C.PLATFORMS:
        raise OpError(f"platform {platform!r}: one of {', '.join(C.PLATFORMS)}")
    caps = C.PLATFORMS[platform]
    if platform in ('twitch', 'kick', 'youtube', 'owncast', 'file') and not channel:
        raise OpError(f"{platform} needs channel= ("
                      + {'twitch': "the channel's login name", 'kick': "the channel's slug, as in kick.com/<slug>",
                         'youtube': "the channel handle (@name) or id", 'owncast': "the server address, https://...",
                         'file': "the .flv path to record to"}[platform] + ")")
    if script is not None and platform != 'fake':
        raise OpError("script= is for the fake venue only")
    for s in script or []:
        if not isinstance(s, dict) or not (s.get('text') or s.get('event')):
            raise OpError("each script step is {after_s, user, text} or {after_s, user, event}")
    if ingest and not str(ingest).startswith(('rtmp://', 'rtmps://')):
        raise OpError("ingest: an rtmp:// or rtmps:// address, without the stream key (that goes in with "
                      "`python -m ismail.stream key <venue>`)")
    if ingest and any(len(part) > 20 for part in str(ingest).split('/')[3:]):
        raise OpError("ingest looks like it has a stream key in it: give the address only; the person adds the key "
                      "with `python -m ismail.stream key <venue>`")
    if (reader is not None or poll_s is not None) and platform != 'youtube':
        raise OpError("reader= and poll_s= are for youtube venues (the other sites' chat is realtime already)")
    if reader is not None and reader not in ('stream', 'poll'):
        raise OpError("reader: 'stream' (realtime, the default) or 'poll' (a list call every poll_s seconds)")
    if poll_s is not None and not 5 <= float(poll_s) <= 600:
        raise OpError("poll_s: 5 to 600 seconds; each read costs 5 of YouTube's 10,000 units a day")
    note_g = guards.check_private(note) if note else ''
    venues = C.load_venues()
    v = {'name': name, 'platform': platform}
    for k, val in (('channel', channel), ('ingest', ingest), ('script', script), ('loop', loop), ('reader', reader),
                   ('poll_s', poll_s), ('note', note)):
        if val is not None:
            v[k] = val
    v['chat'] = bool(chat) and caps['chat']
    v['output'] = caps['output'] if output is None else (bool(output) and caps['output'])
    was = venues.get(name)
    venues[name] = v
    C.save_venues(venues)
    if _port() and _up(_port()):
        _call('reload')
    need = _needs(name, v)
    return guards.with_note((f"{'changed' if was else 'added'} venue {name}: {platform} {channel or ''}, chat "
                             f"{'on' if v['chat'] else 'off'}, {'streams' if v['output'] else 'no output'}.\n"
                             + (need or 'nothing more needed.')), note_g)


def _needs(name, v):
    k = C.key_summary(name, v['platform'])
    lines = []
    if v.get('chat') and v['platform'] in ('twitch', 'youtube', 'kick', 'owncast') and not k.get('login'):
        what = ('read-only: it reads chat but cannot answer' if v['platform'] in ('twitch', 'kick') else
                'its chat cannot be read or answered')
        lines.append(f"no login yet, so {what}. The person types in a terminal: python -m ismail.stream login {name}")
    if v.get('output') and v['platform'] not in ('file',) and not k.get('stream_key'):
        lines.append(f"no stream key yet, so it cannot be streamed to. The person types: python -m ismail.stream key "
                     f"{name}")
    return '\n'.join(lines)


@op()
def venue_list() -> str:
    """Every venue: site, channel, chat on or off, whether it streams, and what it still needs from the person."""
    venues = C.load_venues()
    if not venues:
        return ("no venues yet: venue_set(name='practice', platform='fake') makes a local practice chat; "
                "venue_set(name='twitch', platform='twitch', channel='<login>') adds a real one")
    out = []
    for name, v in sorted(venues.items()):
        out.append(f"{name}: {v['platform']} {v.get('channel') or ''}, chat {'on' if v.get('chat') else 'off'}, "
                   f"{'streams' if v.get('output') else 'no output'}; keys {_keys_line(C.key_summary(name, v['platform']))}")
        need = _needs(name, v)
        if need:
            out += ['  ' + x for x in need.split('\n')]
    return '\n'.join(out)


@op(mutates=True)
def venue_remove(name: str) -> str:
    """Remove a venue's config (its chat closes). Its keys file stays where the person put it."""
    venues = C.load_venues()
    if name not in venues:
        raise OpError(f"no venue {name!r}: venue_list shows them")
    del venues[name]
    C.save_venues(venues)
    if _port() and _up(_port()):
        _call('reload')
    kept = C.keys_path(name).exists()
    return f"removed venue {name}" + (f" (its keys stay in {C.keys_path(name)})" if kept else '')


# ------------------------------------------------------------------ chat

def _cursor_path(who):
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in who)[:60] or 'agent'
    return C.HOME / 'cursors' / f'{safe}.json'


def _cursor_load(who):
    try:
        return int(json.loads(_cursor_path(who).read_text(encoding='utf8'))['since'])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _cursor_save(who, since):
    p = _cursor_path(who)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps({'who': who, 'since': since, 'saved': time.time()}), encoding='utf8')
    os.replace(tmp, p)


@op()
def chat_watch(who: str, since: int = None, venues: list = None, wait_min: float = 100,
               include_self: bool = False) -> str:
    """The standing chat watcher: blocks until chat arrives on any venue, returns it, and remembers its place. Reach
    for it when you read a stream's chat: what viewers write on every site, in one place. Run it
    in the background (`python -m ismail chat_watch "who=MC"`), act on what it returns, and re-arm it FIRST after
    every wake. Returns JSON {lines, since}: each line {n, kind (chat, event, status), venue, platform, user {id,
    name, display, mod, sub, owner}, text, id}; events carry type (follow, sub, cheer, raid, superchat, member,
    redeem) and amount; a line of our own account (its echo) carries own: true. A burst is gathered with a 3 s second look. Your own replies' echoes stay out
    (include_self=true keeps them). venues: only these. wait_min: after this long with nothing it returns
    {alive: true}: re-arm. Answer a line with chat_say(text, reply_to=<its n>)."""
    if since is None:
        since = _cursor_load(who)
    end = time.time() + float(wait_min) * 60
    note = None
    while True:
        left = end - time.time()
        res = _call('listen', timeout=40, who=who, since=since, wait=min(25.0, max(0.0, left)), venues=venues,
                    include_self=include_self, back_s=300 if since is None else 0)
        if since is None:
            note = (f"no saved cursor for {who}: started 5 minutes back, so the room's opening is here; chat_read "
                    f"shows older lines")
        since = res['since']
        if res['lines']:
            more = _call('listen', timeout=15, who=who, since=since, wait=3.0, venues=venues,
                         include_self=include_self)
            lines = res['lines'] + more['lines']
            since = more['since']
            _cursor_save(who, since)
            out = {'who': who, 'since': since, 'lines': lines}
            if note:
                out['note'] = note
            out['next'] = (f're-arm FIRST, in the background: python -m ismail chat_watch "who={who}" (cursor saved '
                           f'at {since}); then answer with chat_say(text, reply_to=<n>)')
            return json.dumps(out, ensure_ascii=False)
        if left <= 0:
            _cursor_save(who, since)
            return json.dumps({'who': who, 'since': since, 'lines': [], 'alive': True,
                               'next': f're-arm FIRST: python -m ismail chat_watch "who={who}"; then stream_status'})


@op()
def chat_read(since: int = 0, venue: str = None, user: str = None, limit: int = 50) -> str:
    """Chat history from the log, newest `limit` lines after `since`: chat, events and our own replies (kind said,
    with by and ok; ok false was refused and never shown). venue / user: only those. Use it to catch up after a gap,
    or to see what was already answered; then chat_watch(who, since=<its last>) so nothing is answered twice."""
    res = _call('read', since=since, venue=venue, user=user, limit=limit)
    return json.dumps(res, ensure_ascii=False)


@op()
def chat_say(text: str, venue: str = None, reply_to: int = None, who: str = None) -> str:
    """Say one line in chat. Reach for it when you reply to a viewer, answer someone in chat, or greet the room.
    reply_to=<a chat line's n> answers that person where they wrote it (threaded on Twitch
    and Kick, '@name ...' on YouTube and others). venue='<name>' says it to one venue; venue='all' to every venue
    whose chat is reading. who: your role, logged with the line. Refused with the reason when it is too long for the
    site, the same line was said there in the last 30 s, or the venue is sending too fast."""
    note = guards.check_private(text)
    res = _call('say', text=text, venue=venue, reply_to=reply_to, who=who)
    lines = [f"{r['venue']}: {'said (n=' + str(r['n']) + ')' if r['ok'] else 'NOT said: ' + r['error']}" for r in res]
    return guards.with_note('\n'.join(lines), note)


# ------------------------------------------------------------------ output

@op(mutates=True)
def stream_go(venues: list, approved: str = None, video: dict = None) -> str:
    """Start streaming the live set to these venues (names, or 'all' for every venue that streams). Going live on a
    public site is the person's yes, every time: approved = their words, as they said them ("go live on twitch
    and youtube"), logged with the time. A `file` venue (a local recording) needs no yes. Every venue's key is
    checked before anything starts. Already-live venues are left as they are, so adding a site mid-set is
    stream_go(['kick'], approved=...). video: picture settings for the start (as stream_visual). The sound is the
    newest live engine's master; while no engine plays, the stream carries silence and the picture."""
    if isinstance(venues, str):
        venues = [v.strip() for v in venues.split(',') if v.strip()]
    if approved:
        guards.check_private(approved)
    return _out_text(_call('go', timeout=60, venues=venues, approved=approved, video=video))


@op(mutates=True)
def stream_end(venues: list = None) -> str:
    """Stop streaming to these venues (default: all; the encoder stops with the last one). Chat stays connected."""
    if isinstance(venues, str):
        venues = [v.strip() for v in venues.split(',') if v.strip()]
    return _out_text(_call('end', timeout=30, venues=venues))


@op(mutates=True)
def stream_visual(mode: str = None, colors: list = None, background: str = None, image: str = None,
                  window: str = None, area: list = None, text_color: str = None, font_size: int = None,
                  chat_overlay: bool = None, width: int = None, height: int = None, fps: int = None,
                  video_kbps: int = None, audio_kbps: int = None, encoder: str = None, capture: str = None) -> str:
    """Set the stream's picture with raw numbers. While live this restarts the encoder: a 2-3 s blip on every site,
    so set everything in one call. mode: page (the visual page, drawn from the set's levels as stream_scene sets
    it: the one to change live with no blip), cqt (a piano-roll spectrum), spectrum (bars), waves, scope (a stereo
    vector scope) or none. colors: ['#rrggbb', '#rrggbb'] for spectrum and waves. background: '#rrggbb', or
    image: a still behind it, or window: the title of a window to capture (a page the agent draws, the VR stage
    mirror). area: [x, y, w, h] of the visualizer, fractions of the frame. chat_overlay: the newest chat line in
    the corner. width/height/fps: 1280x720 at 30 by default. video_kbps: 4500 (Twitch takes 6000 at most).
    encoder: auto (NVENC when the GPU has it), nvenc or x264. capture: how a window (or the page) is captured:
    auto (wgc where ffmpeg has it), wgc (Windows Graphics Capture: GPU-drawn pages such as the stage's WebGL, and a
    covered window, come through; the page draws on the GPU) or gdi (gdigrab: a GPU-drawn window comes out blank, so
    the page draws in software). A Chrome or Edge window is cropped to its web content. Look at the result with
    stream_look. An encoder that starts three times in a row with no picture stops the stream; stream_status says
    why and what to call next."""
    params = {k: v for k, v in dict(mode=mode, colors=colors, background=background, image=image, window=window,
                                     area=area, text_color=text_color, font_size=font_size,
                                     chat_overlay=chat_overlay, width=width, height=height, fps=fps,
                                     video_kbps=video_kbps, audio_kbps=audio_kbps, encoder=encoder,
                                     capture=capture).items()
              if v is not None}
    res = _call('visual', timeout=30, **params)
    v = res['visual']
    return (f"picture: {v['mode']}, {v['width']}x{v['height']} at {v['fps']}, background "
            f"{v.get('image') or v.get('window') or v['background']}, colours {v['colors']}, area {v['area']}"
            + ('; restarted the encoder (a short blip on every site)' if res['restarted'] else
               '; it applies on the next stream_go'))


@op(mutates=True)
def stream_text(slot: str, text: str) -> str:
    """Put a line of text on the stream, live, with no blip. slot: title (top left, large), note (under it), now
    (bottom left: the piece playing) or chat (bottom right; chat_overlay writes it on its own). text: '' clears."""
    note = guards.check_private(text)
    res = _call('text', slot=slot, text=text)
    return guards.with_note(f"{res['slot']}: {res['text']!r}", note)


@op(mutates=True)
def stream_scene(layers: list = None, background=None, trails: float = None, replace: bool = True) -> str:
    """Set the visual page's picture with raw numbers; it changes on the page (and on a stream in mode='page') at
    once, with no blip. Reach for it when you change what viewers see move with the music.
    layers: a list drawn in order, each {kind, x, y, w, h (fractions of the frame), ...}:
      bars {bands: [lo, hi], colors, gap, mirror, smooth, gain}: one bar per band;
      ring {x, y (centre), r, h (bar length), bands, thickness, spin (degrees per beat)};
      pulse {x, y, r0, r1, drive, decay, fill}: a circle that swells with its drive;
      line {bands, thickness, fill}: the spectrum as a line;
      hits {tracks: {'<track>': '#rrggbb'}, shape (circle, square, bar), decay}: a light per track that flashes on
        each of its notes;
      particles {x, y, count, speed, size, spawn (what bursts them: beat, track:<name>, band:<i>)};
      text {text, x, y, size, align, drive}; grid {cols, rows, scroll (rows per beat), drive}.
    Every layer also takes color or colors (a gradient, '#rrggbb'), opacity, blend (source-over, lighter, screen,
    multiply, overlay), smooth (0-1) and gain. drive: level, beat, band:<0-15>, bands:<lo>-<hi> or track:<name>.
    The 16 bands run log-spaced from 40 Hz to 16 kHz: 0-3 sub and bass, 4-7 low mids, 8-11 mids, 12-15 air.
    background: '#rrggbb' or a 2-3 colour gradient top to bottom. trails: 0-0.98, how long motion lingers.
    replace=False adds the layers on top of the current ones. A wrong setting is refused with what it takes."""
    res = _call('scene', layers=layers, background=background, trails=trails, replace=replace)
    sc = res['scene']
    kinds = ', '.join(l['kind'] + (f" ({l['drive']})" if l.get('drive') else '') for l in sc['layers'])
    return (f"scene v{res['v']}: {len(sc['layers'])} layers ({kinds or 'none'}), background {sc['background']}, "
            f"trails {sc['trails']}. See it: stream_page()")


@op()
def stream_page() -> str:
    """The visual page: its address (open it in any browser on this machine, or capture it), the scene it draws,
    and what drives it right now: the level, the 16 bands, the beat and the tracks that just played. Stream it with
    stream_visual(mode='page') (it opens the page in its own window and captures it)."""
    res = _call('page', timeout=15)
    f, sc = res['frame'], res['scene']
    bands = ' '.join(f'{b:.2f}' for b in f.get('bands', []))
    hits = ', '.join(h['track'] for h in f.get('hits') or []) or 'none this frame'
    eng = (f"engine :{f['engine']}, beat {f.get('beat')}, {f.get('bpm')} bpm" if f.get('engine') else
           'no live engine: the page holds still')
    win = res.get('window') or {}
    return '\n'.join([f"page: {res['url']} ({'open in its stream window' if win.get('open') else 'no stream window'})",
                      f"scene: {len(sc['layers'])} layers, " + ', '.join(l['kind'] for l in sc['layers']),
                      f"now: {eng}; level {f.get('level')} ({f.get('rms_db')} dB rms)",
                      f"bands 0-15: {bands}", f"notes just played: {hits}"])


@op()
def stream_look() -> str:
    """How the stream is doing: each venue (live, reconnecting with the site's error, MB sent), the sound source,
    the encoder, the text lines, and the path of a preview frame (refreshed every 2 s): Read it to see the picture."""
    st = _call('look')
    out = [_out_text(st)]
    out.append('text: ' + '; '.join(f"{k} {v!r}" for k, v in st.get('texts', {}).items() if v))
    if st.get('preview'):
        out.append(f"preview frame ({st['preview_age_s']} s old): {st['preview']}")
    return '\n'.join(out)


def _out_text(st):
    lines = [st.get('summary') or st.get('state', 'off')]
    for n, p in (st.get('venues') or {}).items():
        lines.append(f"  {n}: {p['state']}, {p['sent_mb']} MB sent" + (f", {p['restarts']} restarts" if
                     p.get('restarts') else '') + (f"; {p['error']}" if p.get('error') else ''))
    if st.get('encoder_error'):
        lines.append(f"  encoder: {st['encoder_error']}")
    return '\n'.join(lines)
