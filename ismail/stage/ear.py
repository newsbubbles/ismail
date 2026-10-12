"""A listener that keeps running: `python -m ismail.stage.ear --who "crossroads film"`.

2026-10-11: a session listened by re-arming a one-shot watcher after every note, then sat ten minutes on a render;
four notes went unanswered and the user left the headset. This one long-polls /live/inbox for every scene, as a
named listener (the headset shows "listening: <who>" and "heard by <who>" on each note it is handed), until it is
stopped. Run it in the background and read its output as it comes: it prints one line per event that needs an
answer (voice notes, errors, a session that looks stuck) and logs the rest, so an agent is woken only for those.

It survives server restarts (the inbox counter starts again: it resumes from 0), and keeps its place in a mark file,
so a watcher started again within 15 minutes does not skip notes sent while none ran. --once exits after the first printed batch,
for a harness that only wakes the agent when a background job ends.
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

WAKE = ('voice_message', 'perform_clip', 'behaviour_error', 'page_error', 'object_message', 'session_stuck')
WAIT_S = 25                  # each long poll; a listener is fresh while it polls at least every 40 s
MARK_FRESH_S = 900           # a watcher started again within this resumes from its mark; later, from now


def line(e):
    """One printed line: [scene] VOICE #id: text, or [scene] type {details}."""
    sc = e.get('scene') or '?'
    if e.get('type') == 'voice_message':
        return f"[{sc}] VOICE #{e.get('id')}: {e.get('text') or ''}"
    rest = {k: v for k, v in e.items() if k not in ('seq', 'id', 'ts', 'page', 'type', 'scene')}
    return f"[{sc}] {e.get('type')} #{e.get('id')} {json.dumps(rest, separators=(',', ':'), default=str)[:300]}"


def base_url(port=None):
    """The stage server: the port given, else the registry's server for a real scenes folder (not a test copy)."""
    if port:
        return f'http://127.0.0.1:{int(port)}'
    from . import link
    srv = link.servers()
    real = [r for r in srv if not any(p.startswith('.') and p not in ('.', '..') for p in Path(r['scenes']).parts)]
    if not (real or srv):
        raise OSError('no stage server is running')
    return f"http://127.0.0.1:{(real or srv)[0]['port']}"


def get(url, path, q, timeout):
    with urllib.request.urlopen(f'{url}/{path}?{urllib.parse.urlencode(q)}', timeout=timeout) as r:
        return json.loads(r.read() or b'null')


def state_dir(who):
    return Path.home() / '.ismail' / 'ears' / (re.sub(r'[^A-Za-z0-9_-]+', '_', who).strip('_') or 'ear')


def listen(who, port=None, wake=WAKE, once=False, folder=None, out=print, polls=None):
    """The loop. out(line) gets each printed line; polls= stops after that many polls (tests)."""
    folder = Path(folder) if folder else state_dir(who)
    folder.mkdir(parents=True, exist_ok=True)
    mark, logf = folder / 'mark', folder / 'ear.log'

    def log(text):
        with open(logf, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%m-%d %H:%M:%S ') + text + '\n')

    since, started, down, n = None, None, False, 0
    while polls is None or n < polls:
        n += 1
        try:
            url = base_url(port)
            if since is None:                      # resume where the last watcher stopped: the mark is "<seq> <started>"
                head = get(url, 'live/inbox', {'who': who}, 10)
                now, started = head['last'], head.get('started')
                try:                               # a mark from a watcher that stopped long ago: those notes are stale
                    seq, at = (mark.read_text().split() + [None])[:2]
                    fresh = time.time() - mark.stat().st_mtime < MARK_FRESH_S
                    since = (int(seq) if str(started) == str(at) else 0) if fresh else now   # another server start: from 0
                except (OSError, ValueError):
                    since = now
                since = min(since, now)
                log(f'[ear] listening as "{who}" on {url} from inbox {since} (server at {now})')
            r = get(url, 'live/inbox', {'who': who, 'since': since, 'wait': WAIT_S}, WAIT_S + 15)
            if r.get('started') != started or r['last'] < since:     # the server restarted between polls
                log(f'[ear] the server restarted; its inbox from 0')
                since, started = 0, r.get('started')
                continue
            if down:
                log('[ear] server back')
                down = False
            woke = False
            for e in r['events']:
                text = line(e)
                log(text)
                if e.get('type') in wake:
                    out(text)
                    woke = True
            since = max(since, r['last'])
            mark.write_text(f'{since} {started}')
            if once and woke:
                return 0
        except (OSError, ValueError, KeyError) as x:
            if not down:
                out(f'[ear] stage server unreachable ({type(x).__name__}); retrying every 3 s')
                down = True
            since = None                           # re-read where the server is when it comes back
            time.sleep(3)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m ismail.stage.ear', description=__doc__.split('\n\n')[0])
    ap.add_argument('--who', required=True, help='your name as the headset shows it, e.g. "crossroads film"')
    ap.add_argument('--port', type=int, help='the stage server port (default: the running server for real scenes)')
    ap.add_argument('--wake', help='comma-separated event types to print (default: ' + ','.join(WAKE) + ')')
    ap.add_argument('--once', action='store_true', help='exit after the first printed batch')
    ap.add_argument('--dir', help='where the mark and ear.log go (default ~/.ismail/ears/<who>)')
    a = ap.parse_args(argv)
    wake = tuple(w.strip() for w in a.wake.split(',') if w.strip()) if a.wake else WAKE
    try:
        return listen(a.who, a.port, wake, a.once, a.dir, out=lambda s: print(s, flush=True))
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
