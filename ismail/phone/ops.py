"""phone_* ops: agents drive the phone page and read what the person sends from it (server.py says how it works)."""
import json
import re
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from ..api import OpError, op
from . import server as S


def _port():
    try:
        return int(json.loads((S.HOME / 'server.json').read_text(encoding='utf8'))['port'])
    except (OSError, ValueError, KeyError):
        return None


def _call(op_name, timeout=30, **args):
    port = _port()
    if port is None:
        raise OpError("the phone server is not running: phone_start() first (it relays the live set to the phone)")
    req = urllib.request.Request(f'http://127.0.0.1:{port}/agent', headers={'Content-Type': 'application/json'},
                                 data=json.dumps({'op': op_name, 'args': {k: v for k, v in args.items()
                                                                          if v is not None}}).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        res = json.loads(e.read() or b'{}')
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise OpError(f"the phone server does not answer ({e}): phone_start() again (log: {S.HOME / 'server.log'})")
    if not res.get('ok'):
        raise OpError(res.get('error', 'phone server error'))
    return res['result']


def _tailnet(port):
    """The tailnet https address for the port, when `tailscale serve` proxies it; else the command that would."""
    try:
        flags = 0x08000000 if os.name == 'nt' else 0
        st = json.loads(subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True, timeout=10,
                                       creationflags=flags).stdout or '{}')
        host = (st.get('Self') or {}).get('DNSName', '').rstrip('.')
        sv = subprocess.run(['tailscale', 'serve', 'status'], capture_output=True, text=True, timeout=10,
                            creationflags=flags).stdout
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, "tailscale is not installed or not running: the phone can only reach the page on this machine"
    if host and f'{host}:{port} ' in sv + ' ' and f'127.0.0.1:{port}' in sv:
        return f'https://{host}:{port}/', ''
    return None, (f"the tailnet does not reach it yet: run `tailscale serve --bg --https={port} http://127.0.0.1:{port}` "
                  f"once (it stays; tailnet only, never public), then the phone opens https://{host or '<this pc>'}:{port}/")


@op()
def phone_start(port: int = 8870, inbox: str = None) -> str:
    """Start the phone page server (idempotent): the live set in the person's pocket over the tailnet. It relays the
    newest live engine's master as an mp3 stream that keeps playing with the phone's screen off, and takes the
    person's taps and voice notes back, each stamped with the bar they heard. Never starts a set or plays sound here.
    inbox: also write what they send to this file (default: the playing engine's <project>/notes/phone_inbox.jsonl).
    Returns the address to give them. Read what they send with phone_listen, or watch the inbox file."""
    if _port():
        try:
            st = _call('status', timeout=5)
            if inbox:
                _call('route', inbox=inbox)
            url, hint = _tailnet(_port())
            return f"already running: {url or hint}\n{st}"
        except OpError:
            pass
    S.HOME.mkdir(parents=True, exist_ok=True)
    if not S.ffmpeg():
        raise OpError("ffmpeg is not on PATH (or $ISMAIL_FFMPEG): the phone stream needs it (winget install -e --id "
                      "Gyan.FFmpeg)")
    log = open(S.HOME / 'server.log', 'a', encoding='utf8')
    args = [sys.executable, '-m', 'ismail.phone.server', '--port', str(port)] + (['--inbox', inbox] if inbox else [])
    flags = (0x00000008 | 0x00000200 | 0x08000000) if os.name == 'nt' else 0   # detached, own group, no window
    subprocess.Popen(args, stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=flags,
                     cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     start_new_session=os.name != 'nt')
    for _ in range(50):
        time.sleep(0.2)
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as r:
                if json.loads(r.read()).get('ok'):
                    break
        except (OSError, ValueError):
            continue
    else:
        raise OpError(f"the phone server did not come up on {port}: read {S.HOME / 'server.log'}")
    url, hint = _tailnet(port)
    return (f"phone page up: http://127.0.0.1:{port}/ here" + (f", {url} on the tailnet" if url else '') +
            (f"\n{hint}" if hint else '') +
            "\nTell the person: open it, press play, put the phone away; hold the mic to talk. Then phone_listen "
            "(or watch the inbox) for what they send.")


@op()
def phone_stop() -> str:
    """Stop the phone page server (the person's stream ends)."""
    port = _port()
    if port is None:
        return 'the phone server was not running'
    pid = json.loads((S.HOME / 'server.json').read_text(encoding='utf8')).get('pid')
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=10)
        else:
            os.kill(int(pid), 15)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        (S.HOME / 'server.json').unlink()
    except OSError:
        pass
    return f'stopped the phone server (pid {pid})'


@op()
def phone_restart(why: str = 'updating', when_idle: bool = False) -> str:
    """Restart the phone server (after a merge: it serves the new page code) without dropping the person: the page
    is told first ("updating, back in a few seconds"), reconnects the stream as soon as the server answers, and
    reloads itself into the new page code when it is next on screen and idle. when_idle=True restarts only when
    nobody is on the stream (otherwise it says so and does nothing)."""
    if _port() is None:
        return phone_start()
    st = _call('status', timeout=10)
    m = re.match(r'phone server: (\d+) listening', st)
    listening = int(m.group(1)) if m else 0
    if when_idle and listening:
        return f"not restarted: {listening} on the stream; try again later or without when_idle (the page reconnects)"
    told = _call('restarting', back_in_s=5, why=why)
    time.sleep(2.0)                                      # the page's long poll picks the notice up first
    stopped = phone_stop()
    started = phone_start()
    return f"{told}; {stopped}; {started.splitlines()[0]}"


@op()
def phone_status() -> str:
    """Who is listening on the phone, the bar they hear and how far behind the room, the engine, the inbox, voice
    notes waiting for transcription, and what the page shows."""
    return _call('status')


@op()
def phone_listen(who: str, since: int = None, wait: float = 25, page: bool = False) -> str:
    """What the person sent from the phone, oldest first, as JSON {since, lines}: taps (love = cut a highlight,
    change = change it up now, energy_up/energy_down, louder/quieter, pause/resume, start_set), mood (calm, steady,
    lift, peak), voice (then voice_text with the words, same id), answer / exam (to phone_ask, phone_panel_show,
    phone_exam), button (phone_buttons). Every line carries heard {bar, beat, of} (what they actually heard, not the
    engine's now) and behind_s. who: your name (the page shows who is listening while you call at least every 90 s).
    since: the last call's `since` (default: only new lines); wait: seconds to wait for one. Every line also
    carries room {bar, of} (the engine's bar then, there even when the page is off the stream) and now (the piece).
    page=True also returns the page's own actions (kind 'page': open with the device, listen, stop, hidden/visible,
    scroll to a section, download, clip play, panel open/close, note start/end); phone_timeline reads them best."""
    return _call('listen', timeout=float(wait or 0) + 15, who=who, since=since, wait=wait, page=page)


@op()
def phone_timeline(minutes: float = 15, kinds: list = None, limit: int = 200) -> str:
    """A take of the phone session: everything from the phone in the last `minutes` on one clock, oldest first, one
    line each: the time, the bar in the room (and what the phone heard), then what happened: the page's own actions
    (opened, on which device; Listen and Stop; hidden and back; which section they scrolled to; downloads; clips
    played; panels), taps, moods, voice notes with their length and what they said. A line '-- <piece>' marks where
    the piece changed. Use it to lay what they said over what they did. kinds: only these (page, tap, mood, voice,
    voice_text, answer, exam, button)."""
    return _call('timeline', minutes=minutes, kinds=kinds, limit=limit)


@op()
def phone_say(text: str, speak: bool = False, pin: bool = False, buzz: bool = False, voice: str = None,
              sender: str = None) -> str:
    """A caption on the phone. speak=True also says it into the stream (Kokoro, the music ducked under it), so they
    hear it in their pocket: only to answer something they said, never unprompted. pin=True keeps it at the top
    (a "since you left" summary). buzz=True vibrates the phone if the page is open."""
    return _call('say', text=text, speak=speak, pin=pin, buzz=buzz, voice=voice, who=sender)


@op()
def phone_now(now: str = None, next: str = None, recording_why: str = None, mood: str = None,
              now_mark: str = None, next_mark: str = None, length: float = None, sections: list = None,
              into: float = None, sender: str = None) -> str:
    """What the page shows as now playing and next up (default: read from the engine), and why recording is on or
    off (the page always shows whether it is). '' clears a field. mood: set the mood chip (calm, steady, lift, peak).
    now_mark/next_mark: a small mark beside the piece, so they know what they are hearing: 'loved' (one they loved
    before, played again), 'replay' (played earlier, back again), 'new' (just made). Set it on every chapter change;
    without one the page shows a heart when they tapped Love this while that piece played.
    length: the piece's length in seconds and sections: [{'at_s': 0, 'label': 'intro'}, {'at_s': 64, 'label':
    'drop'}, ...]: the page keeps a position line on screen (elapsed / length, the next section). into: seconds into
    the piece now, when it did not start as its name went up."""
    return _call('now', now=now, next=next, recording_why=recording_why, mood=mood, now_mark=now_mark,
                 next_mark=next_mark, length=length, sections=sections, into=into, who=sender)


@op()
def phone_panel_show(panel_id: str = None, title: str = '', text: str = '', image: str = None, buttons: list = None,
                     inputs: list = None, wait: float = 0, sender: str = None) -> str:
    """A panel over the phone page (the stage_panel_show shape): title, text, an image file, buttons (labels), and
    inputs for a fuller answer: [{'id', 'kind': 'choice' (one of options) | 'check' (any of options) | 'toggle' (on
    or off) | 'text', 'label', 'options'}]. A tap arrives as kind 'answer' {id, answer, values: {input id: value},
    for: sender}; wait=N blocks up to N seconds for it. Every panel has a record button: what they say on it arrives
    as kind 'voice' and 'voice_text' with panel=<id> and for=<sender>, so pass sender (your name) to get it back.
    A panel never interrupts a voice note: it waits until they stop recording."""
    return _call('panel_show', timeout=float(wait or 0) + 15, panel_id=panel_id, title=title, text=text, image=image,
                 buttons=buttons, inputs=inputs, wait=wait, who=sender)


@op()
def phone_panel_close(panel_id: str) -> str:
    """Close a panel, question or exam on the phone."""
    return _call('panel_close', panel_id=panel_id)


@op()
def phone_ask(text: str, wait: float = 0, sender: str = None) -> str:
    """A yes/no question on the phone; the answer arrives as kind 'answer'; wait=N blocks up to N seconds for it."""
    return _call('ask', timeout=float(wait or 0) + 15, text=text, wait=wait, who=sender)


FLOORS = ('64k', '128k', 'same')      # floor pairs: the real clip against a 64 or 128 kb/s MP3 copy, or itself
REAL_CLASSES = ('real', 'record', 'recording', 'ref', 'reference')


def _floor_pairs(clips, key, floor, floor_from, exam_id, answers_path):
    """Device floor pairs (ledger:M173, from the Voice and Paper agents): the real clip against an MP3 copy of itself,
    or against itself, so a "can't tell" on the phone can be told apart from what the phone and the ear cannot
    separate at all. The full set on the first floor round (or floor='full': a new device, or a floor answer that
    changed), then one rotating pair, at seeded random places, never first; the pairs are renumbered.
    -> (clips, key, record)."""
    import random
    import shutil
    pat = re.compile(r'^(\d+)([AB])$')
    cl = [dict(c) for c in clips]
    if not cl or not all(pat.match(str(c.get('label', ''))) for c in cl):
        raise OpError("floor pairs need clips labelled by pair (1A, 1B, 2A, 2B ...); label them so, or leave floor "
                      "off")
    src = floor_from
    if src is None:
        src = next((c['path'] for c in cl if str((key or {}).get(c['label'], '')).lower() in REAL_CLASSES), None)
    if not src or not os.path.isfile(str(src)):
        raise OpError(f"floor pairs need a real clip: give floor_from=<path>, or key a clip as one of "
                      f"{list(REAL_CLASSES)}")
    ff = S.ffmpeg()
    if not ff:
        raise OpError('floor pairs need ffmpeg (set ISMAIL_FFMPEG)')
    hist_f = S.HOME / 'floor_rounds.json'
    try:
        hist = json.loads(hist_f.read_text(encoding='utf8'))
    except (OSError, ValueError):
        hist = []
    rates = list(FLOORS) if floor == 'full' or not hist else [FLOORS[len(hist) % len(FLOORS)]]
    eid = exam_id or f'exam_{int(time.time())}'
    rng = random.Random(eid)
    out_d = os.path.join(os.path.dirname(os.path.abspath(answers_path)) if answers_path else str(S.HOME / 'exams'),
                         f'{eid}_floor')
    os.makedirs(out_d, exist_ok=True)

    def run(*a):
        subprocess.run([ff, '-y', '-loglevel', 'error', *a], check=True, capture_output=True)

    groups = {}
    for c in cl:
        groups.setdefault(int(pat.match(c['label']).group(1)), []).append(c)
    order = [sorted(groups[k], key=lambda c: c['label']) for k in sorted(groups)]
    floors = []
    for j, rate in enumerate(rates):
        same = os.path.join(out_d, f'f{j + 1}_1.wav')           # neutral names: nothing says which side is the MP3
        other = os.path.join(out_d, f'f{j + 1}_2.wav')
        run('-i', str(src), same)
        if rate == 'same':
            shutil.copyfile(same, other)
        else:
            mp3 = os.path.join(out_d, f'f{j + 1}_tmp.mp3')
            run('-i', str(src), '-b:a', rate, mp3)
            run('-i', mp3, other)
            os.remove(mp3)
        side = rng.choice('AB')
        pair = [{'path': other}, {'path': same}] if side == 'A' else [{'path': same}, {'path': other}]
        order.insert(rng.randrange(1, len(order) + 1), pair)
        floors.append((pair, rate, None if rate == 'same' else side))
    new, new_key, labels, rec = [], {}, {}, []
    for i, pair in enumerate(order, 1):
        fl = next((f for f in floors if f[0] is pair), None)
        for c, ab in zip(pair, 'AB'):
            lab = f'{i}{ab}'
            new.append({**c, 'label': lab})
            if fl:
                new_key[lab] = 'real'
            else:
                labels[lab] = c['label']
                if key and c['label'] in key:
                    new_key[lab] = key[c['label']]
        if fl:
            rec.append({'pair': i, 'rate': fl[1], 'mp3_side': fl[2]})
    hist.append({'exam_id': eid, 'at': time.strftime('%Y-%m-%d %H:%M'), 'floors': rec})
    hist_f.parent.mkdir(parents=True, exist_ok=True)
    hist_f.write_text(json.dumps(hist, indent=1), encoding='utf8')
    return new, (new_key if key else None), {'exam_id': eid, 'floors': rec, 'labels': labels, 'from': str(src)}


@op()
def phone_exam(title: str, clips: list, question: str = '', chips: list = None, choices: list = None,
               answers_path: str = None, exam_id: str = None, wait: float = 0, key: dict = None,
               secrets: list = None, check: bool = True, sender: str = None, floor: str = None,
               floor_from: str = None) -> str:
    """A blind exam on the phone: clips [{label, path, note?}] each with a play button (the live stream pauses while
    one plays, and rejoins live after), word chips to tick per clip, one choice (e.g. ['A is the record', 'B is the
    record', "can't tell"]), a note, and Submit. The answers arrive as kind 'exam' {id, answers}, and are appended
    to answers_path when given (the exam's own answers file, so no "done" is needed). Label clips blind (A, B).
    It runs exam_check first and refuses on NOT READY: give key={label: class} and secrets=[source names] so the
    blind-leak checks run too. check=False only when the person asked to see it anyway.
    floor='auto' adds device floor pairs, the real clip against a 64 or 128 kb/s MP3 copy of itself or against
    itself: the full set on the first floor round, then one rotating pair; 'full' gives the full set again (a new
    listening device, or a floor answer that changed). Each costs a slot of the 5-6 pair budget. Clips must be
    labelled 1A, 1B, 2A ...; they are renumbered around the floor pairs, and the reply (and <answers_path>.floor.json)
    maps the new labels back to yours and says which side of each floor pair is the MP3. floor_from: the real clip
    (default: the first clip keyed real, record or ref). A floor pair told apart more often than chance means the
    phone and the ear separate even an MP3 copy; a floor never told apart means a "can't tell" on a real pair is
    about the voice, not the device."""
    record = None
    if floor:
        if floor not in ('auto', 'full'):
            raise OpError("floor is 'auto' (the full set the first time, then one rotating pair) or 'full'")
        clips, key, record = _floor_pairs(clips, key, floor, floor_from, exam_id, answers_path)
        if answers_path:
            with open(answers_path + '.floor.json', 'w', encoding='utf8') as f:
                json.dump(record, f, indent=1)
    if check:
        from .. import exam_check as EC
        cl = [c if isinstance(c, dict) else {'path': c} for c in clips or []]
        texts = {'the page text': ' '.join([title, question] + [str(c.get('note', '')) for c in cl]
                                           + [str(c.get('label', '')) for c in cl])}
        ready, lines = EC.run(clips=[{'label': c.get('label') or chr(65 + i), 'path': c.get('path')}
                                     for i, c in enumerate(cl)], key=key, secrets=secrets,
                              answers_path=answers_path, texts=texts)
        if not ready:
            raise OpError('the exam was not shown, its pre-flight failed:\n' + '\n'.join(lines))
    out = _call('exam', timeout=float(wait or 0) + 15, title=title, clips=clips, question=question, chips=chips,
                choices=choices, answers_path=answers_path, exam_id=exam_id, wait=wait, who=sender)
    if record:
        out += ('\nfloor pairs: ' + ', '.join(f"pair {r['pair']} {r['rate']}" + (f" (the MP3 is {r['mp3_side']})"
                                                  if r['mp3_side'] else '') for r in record['floors'])
                + '; your pairs are now ' + ', '.join(f'{k}={v}' for k, v in record['labels'].items()))
    return out


@op()
def phone_offer(path: str, label: str = None, auto: bool = False, title: str = None, album: str = None,
                artist: str = None, sender: str = None) -> str:
    """Offer a file for download on the phone (a render, a take, a PDF). auto=True starts it at once if the page is
    open; otherwise it waits as a card with a Download button. label: what the card says, and the downloaded file's
    name (readable words, not a slug). An mp3 goes out as a tagged copy (their file is never changed): title (default
    the label), artist (default 'ismail'), album (the song or set), the date, and ismail with its GitHub link."""
    return _call('offer', path=os.path.abspath(path), label=label, auto=auto, title=title, album=album, artist=artist,
                 who=sender)


@op()
def phone_buttons(buttons: list = None, sender: str = None) -> str:
    """Extra buttons on the page, as data: [{'id': 'darker', 'label': 'darker'}, ...] or plain labels; [] or none
    clears them. A tap arrives as kind 'button' {id, label, heard}."""
    return _call('buttons', buttons=buttons or [], who=sender)


@op()
def phone_vibe(preset: str = None, ground: str = None, ink: str = None, accent: str = None, heading: str = None,
               image: str = None, blur: int = None, dim: float = None, effect: str = None, intensity: float = None,
               transition_ms: int = None, reset: bool = False, menu: bool = False, layers: list = None,
               hue_drift: float = None, at: str = None, ramp_beats: float = None, save: str = None, scene: str = None,
               cancel_moves: bool = False, react: dict = None, sender: str = None) -> str:
    """Set the phone page's look to fit the music, so the person feels you there (as the stage does in VR): change it
    with the mood, on chapter changes. preset: a starting point (default, rain, calm, warm, night, peak), then any
    part over it. ground/ink/accent: colours ('#rrggbb', 'rgb(r g b)', 'hsl(h s% l%)'); the ground stays dark, ink
    on ground 7:1 and accent 3:1 or it is refused with what to change. heading: the face of the titles (archivo,
    fraunces, playfair, cormorant, space grotesk, syne, unbounded, bebas, major mono). image: a picture file (a cover,
    a Blender still, art another agent made) behind the page, blurred by `blur` px (0-40) and darkened by `dim`
    (0.2-0.9); '' removes it. effect: none, rain, particles, pulse (breathes on the set's beat), grain, aurora, at
    `intensity` 0-1. Changes fade over transition_ms. reset=True starts from the default skin; menu=True lists the
    presets, faces and effects, the current vibe, the saved scenes and the scheduled moves.
    layers: up to 3 effects at once, drawn in order, each a dict: effect, intensity, speed (0.1-4), density (0-1),
    size (0.25-4), angle (rain's slant, -60-60), opacity, color, color2, blend (normal, add, screen, multiply,
    overlay); e.g. [{'effect': 'aurora', 'speed': 0.5}, {'effect': 'rain', 'density': 0.9, 'angle': 25, 'blend':
    'add'}]. effect= alone replaces them with one. hue_drift: degrees a minute the colours turn (0 still).
    at='bar:N': the look lands on bar N as the phone hears it (a drop on its downbeat); each call with at= adds a
    move, building on the last one, and they play in bar order; ramp_beats: fade into it over that many beats.
    cancel_moves=True drops the scheduled ones. save='name' keeps the resulting look as a scene; scene='name'
    starts from a saved one (then any part over it).
    react: the set's own notes drive the page, each on the beat the phone hears it: {track: reaction} with flash,
    glow, burst, sparks, drops or ring, or {track: {'do': 'glow', 'color': '#ff8844', 'amount': 0.8}}; 'qrq*'
    matches every track starting qrq. E.g. {'kick': 'glow', 'qrq': 'sparks', 'stab': 'flash', 'piano': 'drops'}.
    {} turns it off."""
    return _call('vibe', preset=preset, ground=ground, ink=ink, accent=accent, heading=heading, image=image, blur=blur,
                 dim=dim, effect=effect, intensity=intensity, transition_ms=transition_ms, reset=reset or None,
                 menu=menu or None, layers=layers, hue_drift=hue_drift, at=at, ramp_beats=ramp_beats, save=save,
                 scene=scene, cancel_moves=cancel_moves or None, react=react, who=sender)


@op()
def phone_sounds(event: str = None, path: str = None, gain_db: float = 0.0, menu: bool = False,
                 sender: str = None) -> str:
    """Give the phone page its sounds: a short sound you made with ismail (render it, keep it under 5 s and 1 MB:
    wav, ogg or mp3) plays on its event, so the page sounds like the set (Nate: every sound on it is crafted, as a
    design rule). event: message (a phone_say caption arrives), note_start, note_end, note_sent, error (these replace
    the built-in tones), tap (any key that sends, unless it has its own), love, change, mood, offer, panel (a panel,
    question or exam opens), chapter (the piece changes); the stage's earcon names work too (incoming, rec_start,
    rec_stop, sent). path='' clears one. gain_db: -30 to +6 on the page.
    An event with no sound stays silent. menu=True (or no event) lists the events and what each plays now."""
    if path:
        path = os.path.abspath(path)
    return _call('sounds', event=event, path=path, gain_db=gain_db, menu=menu or None, who=sender)


@op()
def phone_unsay(match: str = None, since: str = None, n: int = None) -> str:
    """Take captions back off the phone page, its history and its pinned line: match='text' (several with '|',
    case ignored), since='HH:MM' (today), or n=3 (the last three). Never put where the person lives, their name or
    other personal details on the page: they may be recording the screen."""
    return _call('unsay', match=match, since=since, n=n)


@op()
def phone_hum(voice_id: str = None) -> str:
    """Is a voice note a hum or a sung line, and where is the music he heard under it: any note (the latest when no
    voice_id). A hum is told from the note itself (pitched, holding its notes, few words) and also arrives in the
    inbox as kind 'hum'. Every note keeps the master the page played under it (<id>_ref.wav, from 3 s before to 2 s
    after, with beat stamps): the music bleeding into the mic lines the note up with the beat he heard."""
    return _call('hum', voice_id=voice_id)


@op()
def phone_buzz(pattern: list = None) -> str:
    """Vibrate the phone (if the page is open): pattern in ms, e.g. [200, 100, 200]."""
    return _call('buzz', pattern=pattern)


@op()
def phone_route(inbox: str = None) -> str:
    """Also write what the person sends to this file (a song's notes/phone_inbox.jsonl); none: back to the playing
    engine's project. The route stays across a server restart."""
    return _call('route', inbox=inbox)
