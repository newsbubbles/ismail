"""What went wrong in a live set, kept where the DJ can find it again: every rejected render (the track, clip, bar
and reason, and the call that fixes it) and every burst of underruns (with the ops sent just before it). A reply
that said it once was not enough: the next call of any helper took the news, and the per-track errors were cleared
by the next instrument swap (Live DJ 10-31: "rejected 5", and nothing said which clip or why)."""
import collections
import os
import time

from ..notation import midi_to_name
from .timeline import EPS, fmt_bar

REJECTS_KEPT = 50       # rejected renders live_rejects can list
BURSTS_KEPT = 20        # underrun bursts it can list
OPS_KEPT = 40           # ops remembered, to name what was sent before a burst
BURST_GAP_S = 1.0       # underruns closer together than this are one burst
CAUSE_S = 20.0          # ops sent this long before a burst's first underrun are named beside it
# ops that read the set and change nothing: never the cause of a burst
QUIET_OPS = {'status', 'view', 'clock', 'keys', 'rejects', 'listen', 'controls', 'onsets', 'played', 'take',
             'stream', 'control'}


def sounds_of(inst, pitches=None):
    """The bank sounds an instrument reads (a kit: those of the pads `pitches` hit, all pads when None)."""
    t = inst.get('type')
    if t == 'kit':
        out = []
        for p, sub in (inst.get('map') or {}).items():
            if pitches is None or int(p) in pitches:
                out += sounds_of(sub)
        return out
    if t == 'sampler':
        if inst.get('sound'):
            return [inst['sound']]
        return [s if isinstance(s, str) else s.get('sound') for s in inst.get('sounds') or []]
    if t == 'synth':
        return [o['table'] for o in inst.get('oscs') or [] if o.get('table')]
    return []


def piece(inst, pitch):
    """What a note plays, in the DJ's words: a kit pad's sound ('kit pad D1 = whisper_ghost_01'), else the note."""
    if inst.get('type') == 'kit':
        sub = (inst.get('map') or {}).get(str(int(pitch)))
        if sub is None:
            return f"pad {midi_to_name(int(pitch))} (no pad there)"
        names = sounds_of(sub)
        return f"pad {midi_to_name(int(pitch))} = {names[0] if names else sub.get('type')}"
    return midi_to_name(int(pitch))


def describe_op(op, args):
    """live_track(track='kit', instrument=kit of 62 pads): an op as the DJ sent it, short."""
    parts = []
    for k, v in (args or {}).items():
        if v is None or v is False and k not in ('loop', 'cue'):
            continue
        if isinstance(v, dict) and v.get('type'):
            n = len(v.get('map') or {})
            voice = f":{v['voice']}" if v.get('voice') else ''
            s = f"{v['type']} of {n} pads" if v['type'] == 'kit' else v['type'] + voice
        elif isinstance(v, list) and v and all(isinstance(x, dict) and 'track' in x for x in v):
            tracks = list(dict.fromkeys(x['track'] for x in v))
            s = f"{len(v)} clips on {', '.join(tracks[:4])}" + (' ...' if len(tracks) > 4 else '')
        elif isinstance(v, (list, tuple, dict)):
            s = f"[{len(v)} items]"
        elif isinstance(v, str):
            s = repr(v if len(v) <= 28 else v[:25] + '...')
        else:
            s = f"{v:g}" if isinstance(v, float) else repr(v)
        parts.append(f"{k}={s}")
    return f"live_{op}({', '.join(parts)})"


def fix_for(err, track, inst, root):
    """The next step for a rejected render, where its reason says one."""
    again = f"then live_track('{track}', instrument=<the same>) renders the silent notes again"
    if "not in the bank" in err or "not in the live project's bank" in err:
        return f"sound_import the sound into {root} (or fix its name in the instrument); {again}"
    if err.startswith('InstrumentError'):
        return f"fix the instrument (instrument_help(type='{inst.get('type')}')); {again}"
    if 'NaN' in err or 'peaks at' in err:
        return f"the voice is broken or far too hot: lower its gain_db or fix the voice; {again}"
    if inst.get('type') == 'code':
        return (f"the voice {inst.get('voice') or '(inline code)'} raised it: voice_help(name=...) shows how it is "
                f"called; fix it, {again}")
    return f"tell Main Dev with this line and live_status; {again}"


class Trouble:
    """The engine's log of rejected renders and underrun bursts. The audio thread only appends a timestamp; the
    scheduler closes bursts (check), so nothing here costs the mixer."""

    def __init__(self):
        self.rejects = collections.deque(maxlen=REJECTS_KEPT)
        self.bursts = collections.deque(maxlen=BURSTS_KEPT)
        self.ops = collections.deque(maxlen=OPS_KEPT)
        self.sounded = set()            # (root, sound): bank sounds the set has rendered (a first use loads the file)
        self._open = []                 # (wall time, played sample) of underruns not yet closed into a burst

    # ------------------------------------------------------------------ writers
    def sent(self, op, args, bar):
        if op not in QUIET_OPS:
            self.ops.append((time.time(), bar, describe_op(op, args)))

    def underrun(self, played):
        self._open.append((time.time(), played))      # the audio thread: one append, nothing else

    def reject(self, eng, job, err, users):
        """A render came back with an error: log where it was and why, and say it in the engine log."""
        track = job['track']
        tr = eng.tracks.get(track) or {}
        inst = tr.get('inst') or {}
        where = job.get('where') or {}
        on = where.get('on')
        clips = sorted({k[0] for k in users}) or ([where['clip']] if where.get('clip') else [])
        notes = sum(len(eng.waiting.get(k, [])) for k in users)
        root = tr.get('root') or eng.root
        e = {'t': time.time(), 'beat': on if on is not None else eng.beat(eng.pos),
             'bar': fmt_bar(on, eng.bpb) if on is not None else fmt_bar(eng.beat(eng.pos), eng.bpb) + ' (warm-up)',
             'deck': tr.get('deck'), 'track': track, 'clips': clips, 'notes': notes,
             'what': ', '.join(dict.fromkeys(piece(inst, p) for p in where.get('pitches', [])[:4])),
             'reason': err, 'fix': fix_for(err, track, inst, root)}
        self.rejects.append(e)
        print(f"rejected: {self.line(e)}", flush=True)
        return e

    def check(self, eng, now=None):
        """Close a burst once the underruns have stopped for BURST_GAP_S: log it with what came just before it.
        Returns the news line ('' when no burst closed)."""
        now = time.time() if now is None else now
        if not self._open or now - self._open[-1][0] < BURST_GAP_S:
            return ''
        got, self._open = self._open, []
        t0, p0 = got[0]
        b = eng.beat(p0)
        ops = [(t0 - t, bar, d) for t, bar, d in self.ops if t0 - CAUSE_S <= t <= t0]
        starts = sorted({c.track for c in eng.tl.clips.values() if b - eng.bpb - EPS <= c.start <= b + EPS})
        e = {'t': t0, 'beat': b, 'bar': fmt_bar(int(b), eng.bpb), 'n': len(got), 'span_s': got[-1][0] - t0,
             'ops': ops, 'starts': starts, 'backlog': sum(1 for j in eng.jobs.values() if not j['warm']),
             'mixer_peak': eng.mix_peak}
        self.bursts.append(e)
        line = self.burst_line(e)
        print(f"underruns: {line}", flush=True)
        return line

    # ------------------------------------------------------------------ readers
    @staticmethod
    def line(e):
        deck = f"deck {e['deck']} " if e.get('deck') else ''
        clips = f" clip {', '.join(e['clips'][:3])}" if e['clips'] else ''
        what = f" ({e['what']})" if e['what'] else ''
        n = f", {e['notes']} notes silent" if e['notes'] > 1 else ''
        at = time.strftime('%H:%M:%S', time.localtime(e['t']))
        return f"{e['bar']} ({at}) {deck}track {e['track']}{clips}{what}{n}: {e['reason']}. Next: {e['fix']}"

    @staticmethod
    def burst_line(e):
        at = time.strftime('%H:%M:%S', time.localtime(e['t']))
        head = f"{e['n']} underrun{'s' if e['n'] != 1 else ''} at {e['bar']} ({at})"
        if e['span_s'] >= 0.05:
            head += f" (over {e['span_s']:.1f} s)"
        why = []
        if e['ops']:
            why.append('; '.join(f"{dt:.0f} s after {d}" for dt, _, d in e['ops'][-3:]))
        if e['starts']:
            why.append(f"clips started on {len(e['starts'])} tracks in the bar before ({', '.join(e['starts'][:6])}"
                       + (' ...' if len(e['starts']) > 6 else '') + ")")
        if e['backlog']:
            why.append(f"{e['backlog']} renders waiting")
        if not why:
            why.append(f"no op in the {CAUSE_S:.0f} s before and no clip started near it: other programs on the "
                       f"machine (python -m ismail.machine) or the mixer alone (mixer: line)")
        return head + ': ' + '; '.join(why)

    def status_lines(self, total):
        """The newest rejections and burst for live_status, under the render line."""
        out = []
        if self.rejects:
            newest = list(self.rejects)[-3:]
            out.append(f"rejected renders, newest {len(newest)} of {total} (live_rejects lists the last "
                       f"{REJECTS_KEPT} with what to do):")
            out += ['  ' + self.line(e) for e in reversed(newest)]
        if self.bursts:
            out.append("last underrun burst: " + self.burst_line(self.bursts[-1]))
        return out

    def report(self, bpb, since=None):
        """live_rejects: every rejection and burst kept, oldest first; since=<bar> keeps those from that bar on."""
        b0 = None if since is None else (float(since) - 1) * bpb
        rj = [e for e in self.rejects if b0 is None or e['beat'] >= b0 - EPS]
        bu = [e for e in self.bursts if b0 is None or e['beat'] >= b0 - EPS]
        frm = f" from bar {since:g}" if since is not None else ''
        lines = [f"rejected renders{frm}: {len(rj)}" + ('' if rj else ' (none kept)')]
        lines += ['  ' + self.line(e) for e in rj]
        lines.append(f"underrun bursts{frm}: {len(bu)}" + ('' if bu else ' (none)'))
        lines += ['  ' + self.burst_line(e) for e in bu]
        return '\n'.join(lines)

    def mark_sounded(self, root, inst, pitches=None):
        for s in sounds_of(inst, pitches):
            self.sounded.add((os.path.normcase(root), s))

    def new_sounds(self, root, inst, pitches=None):
        return sorted({s for s in sounds_of(inst, pitches) if (os.path.normcase(root), s) not in self.sounded})
