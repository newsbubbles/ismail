"""The request queue's log, read back: requests.jsonl replayed into {id: request} and the waiting line in order.

Kept apart from request_queue.py (the request_* ops) so the stream server reads the queue for the page's panels
without loading the ops and everything they bring."""
import json

from . import config as C


def log_path():
    return C.HOME / 'requests.jsonl'


def _events():
    try:
        with open(log_path(), encoding='utf8') as f:
            raw = f.readlines()
    except OSError:
        return []
    out = []
    for line in raw:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue        # a line cut by a crash mid-write
    return out


def viewer_key(venue, who):
    """One viewer on one venue, by the name chat shows (any case), so an op given a name and a chat line agree."""
    return f"{venue or ''}:{str(who).strip().lower()}"


def state():
    """{id: request} replayed from the log: who, what, venue, note, status, added, started, ended, the UTC time each
    state was last entered (queued_at, playing_at, done_at, skipped_at, so request-to-air time reads off it), the
    viewer's key, the keys of everyone who voted for it, and the !love taps while it played."""
    reqs = {}
    for e in _events():
        r = reqs.get(e.get('id'))
        if e.get('ev') == 'add':
            reqs[e['id']] = {k: e.get(k) for k in ('id', 'who', 'what', 'venue', 'note')} | {
                'status': 'waiting', 'added': e['at'], 'started': None, 'ended': None, 'queued_at': e.get('ts'),
                'votes': [], 'loves': 0, 'key': viewer_key(e.get('venue'), e.get('who'))}
        elif e.get('ev') == 'vote' and r and e.get('key') not in r['votes']:
            r['votes'].append(e['key'])
        elif e.get('ev') == 'love' and r:
            r['loves'] += 1
        elif e.get('ev') == 'mark' and r:
            r['status'] = e['status']
            r['queued_at' if e['status'] == 'waiting' else e['status'] + '_at'] = e.get('ts')
            if e.get('note'):
                r['note'] = e['note']
            if e['status'] == 'playing':
                r['started'], r['ended'] = e['at'], None
            elif e['status'] in ('done', 'skipped'):
                r['ended'] = e['at']
            else:
                r['started'] = r['ended'] = None
    return reqs


def _order(r):
    return -len(r['votes']), r['id']        # more votes first, then first asked


def line_up(reqs):
    """The waiting requests in the order they play."""
    return sorted((r for r in reqs.values() if r['status'] == 'waiting'), key=_order)
