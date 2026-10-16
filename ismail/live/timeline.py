"""The live queue: clips on tracks, in beats. Pure logic, no audio, no threads.

Bar 1 starts at beat 0 when the engine starts. A clip is a block of notes `length` beats long that repeats
`loop` times (None = until replaced). One rule decides every conflict: a clip queued on a track at beat S
replaces what that track would play from S on. Clips that start at or after S are removed; a clip still
playing at S is cut at S (notes it started before S ring out). A stop is the same rule with nothing after.
A clip or stop may carry a section name (a chapter of a set): cancel_section takes back what a section still had
to do from a beat on, when the set cuts over to something else mid-chapter.
"""
import bisect
import math

from ..notation import fmt_num

QUANTA = {'next_beat': None, 'next_bar': 1, 'next_2': 2, 'next_4': 4, 'next_8': 8, 'next_16': 16}
EPS = 1e-6


class QueueError(ValueError):
    pass


class Clip:
    def __init__(self, cid, track, notes, length, loop, start, at):
        self.id = cid
        self.track = track
        self.notes = sorted(notes)       # [(beat_in_clip, midi, dur_beats, vel)]
        self.length = float(length)
        self.loop = loop                 # int >= 1, or None = forever
        self.start = float(start)
        self.at = at
        self.cut = None                  # beat where a later clip or stop takes over
        self.expr = None                 # {lane: [(beat_in_clip, value)]} for performer voices (bend, vib, ...)
        self.beat0 = 0.0                 # the song beat of the clip's beat 0 (a deck's window start), for performers
        self.context = []                # [(beat < 0, midi, dur, vel)]: what a performer played before the clip
        self.section = None              # the set section (chapter) it was queued for, if named

    @property
    def natural_end(self):
        return None if self.loop is None else self.start + self.loop * self.length

    @property
    def end(self):
        ends = [e for e in (self.natural_end, self.cut) if e is not None]
        return min(ends) if ends else None


def bar_of(beat, bpb):
    return beat / bpb + 1


def fmt_bar(beat, bpb):
    """Beat -> 'bar 12' or 'bar 12 beat 3' (beats 1-based, as counted)."""
    beat = round(beat, 6)           # beats computed back from sample positions land a hair early
    bar = int(math.floor(beat / bpb + EPS)) + 1
    off = beat - (bar - 1) * bpb
    return f"bar {bar}" if abs(off) < EPS else f"bar {bar} beat {fmt_num(off + 1)}"


class Timeline:
    def __init__(self, bpb=4):
        self.bpb = bpb
        self.clips = {}                  # id -> Clip (current and future; finished ones are pruned)
        self.stops = {}                  # track -> [beat]: explicit stops, for display
        self.stop_sections = {}          # track -> {beat: section} for stops queued with a section name
        self._n = 0

    def copy(self):
        """A copy to try a batch on: clips are copied shallowly (their note lists never change; only `cut` does)."""
        import copy as _copy
        t = Timeline(self.bpb)
        t.clips = {k: _copy.copy(c) for k, c in self.clips.items()}
        t.stops = {k: list(v) for k, v in self.stops.items()}
        t.stop_sections = {k: dict(v) for k, v in self.stop_sections.items()}
        t._n = self._n
        return t

    def track_clips(self, track):
        return sorted((c for c in self.clips.values() if c.track == track), key=lambda c: c.start)

    def playing(self, track, beat):
        for c in self.track_clips(track):
            if c.start <= beat + EPS and (c.end is None or beat < c.end - EPS):
                return c
        return None

    def resolve_at(self, at, now, ready):
        """Launch beat for an `at` spec. now = the beat the mixer has reached; ready = earliest beat the first notes
        can be rendered by. Returns (beat, note or None)."""
        at = (at or 'next_bar').strip()
        floor = max(now, ready)
        if at in ('asap', 'now'):         # one vocabulary for every live op: both mean the first quarter beat ready
            return math.ceil(floor * 4 - EPS) / 4, None
        if at in QUANTA:
            q = QUANTA[at]
            step = 1 if q is None else q * self.bpb
            want = math.ceil(now / step - EPS) * step
            if want < now + EPS:
                want += step
            beat = want
            while beat < ready - EPS:
                beat += step
            note = None
            if beat > want + EPS:
                note = f"moved from {fmt_bar(want, self.bpb)} to {fmt_bar(beat, self.bpb)}: its first notes need " \
                       f"~{ready - now:.1f} beats to render"
            return beat, note
        if at.startswith('bar:'):
            try:
                bar = float(at[4:])
            except ValueError:
                raise QueueError(f"at={at!r}: use bar:<number>, e.g. bar:17")
            beat = (bar - 1) * self.bpb
            if beat < now - EPS:
                nxt = math.ceil(now / self.bpb - EPS) + 1
                raise QueueError(f"at={at!r} has already played (now {fmt_bar(now, self.bpb)}); use next_bar or "
                                 f"bar:{nxt} or later")
            note = None
            if beat < ready - EPS:
                note = f"{fmt_bar(beat, self.bpb)} is sooner than the render estimate; its first notes may start late"
            return beat, note
        if at.startswith('after:'):
            cid = at[6:]
            c = self.clips.get(cid)
            if c is None:
                raise QueueError(f"at={at!r}: no queued or playing clip {cid!r}; live_view lists the clips")
            if c.end is None:
                raise QueueError(f"at={at!r}: {cid} loops forever, so it never ends. Give {cid} a loop count "
                                 f"(queue it again with loop=N), or use at='bar:<n>' / 'next_4'")
            note = None
            if c.end < ready - EPS:
                note = f"{cid} ends sooner than the render estimate; the first notes may start late"
            return c.end, note
        raise QueueError(f"at={at!r}: use next_beat | next_bar | next_2 | next_4 | next_8 | next_16 | now (= asap) | "
                         f"bar:<n> | after:<clip id>")

    def claim(self, track, beat):
        """Apply the replace rule on `track` from `beat`. Returns (removed ids, cut ids)."""
        removed, cut = [], []
        for c in self.track_clips(track):
            if c.start >= beat - EPS:
                del self.clips[c.id]
                removed.append(c.id)
            elif c.end is None or c.end > beat + EPS:
                c.cut = beat
                cut.append(c.id)
        self.stops[track] = [s for s in self.stops.get(track, []) if s < beat - EPS]
        return removed, cut

    def add(self, track, notes, length, loop, start, at):
        self._n += 1
        c = Clip(f"c{self._n}", track, notes, length, loop, start, at)
        self.clips[c.id] = c
        return c

    def stop(self, track, beat, section=None):
        self.stops.setdefault(track, []).append(beat)
        tags = self.stop_sections.setdefault(track, {})
        if section:
            tags[beat] = section
        else:
            tags.pop(beat, None)

    def cancel_section(self, section, beat, keep=()):
        """Take back what `section` still has to do from `beat` on: its clips that start there or later go, its
        clips still playing then are cut there (their last notes ring out), and its stops after it are dropped.
        keep: clip ids and (track, stop beat) pairs left alone (a batch that cancels a section may reuse its name).
        A dropped stop does not bring back a clip it had already ended. Returns (removed ids, cut ids,
        [(track, stop beat)])."""
        removed, cut, stops = [], [], []
        for c in sorted(self.clips.values(), key=lambda c: (c.track, c.start)):
            if c.section != section or c.id in keep:
                continue
            if c.start >= beat - EPS:
                del self.clips[c.id]
                removed.append(c.id)
            elif c.end is None or c.end > beat + EPS:
                c.cut = beat
                cut.append(c.id)
        for track, beats in self.stops.items():
            tags = self.stop_sections.get(track, {})
            gone = [s for s in beats if s > beat + EPS and tags.get(s) == section and (track, s) not in keep]
            if gone:
                self.stops[track] = [s for s in beats if s not in gone]
                for s in gone:
                    tags.pop(s, None)
                stops += [(track, s) for s in gone]
        return removed, cut, stops

    def sections(self, now):
        """Section names with a clip or stop still to come or playing after `now`."""
        out = {c.section for c in self.clips.values() if c.section and (c.end is None or c.end > now + EPS)}
        for track, tags in self.stop_sections.items():
            out |= {sec for b, sec in tags.items() if b > now + EPS and b in self.stops.get(track, [])}
        return sorted(out)

    def cancel(self, cid, now):
        c = self.clips.get(cid)
        if c is None:
            raise QueueError(f"no clip {cid!r}; live_view lists the clips")
        if c.start <= now + EPS:
            raise QueueError(f"{cid} is already playing. To stop it, live_queue([{{'track': '{c.track}', "
                             f"'stop': true, 'at': 'next_bar'}}])")
        del self.clips[cid]
        return c

    def prune(self, now):
        """Forget clips that have ended (their last notes may still ring in the mixer)."""
        for cid in [c.id for c in self.clips.values() if c.end is not None and c.end <= now - EPS]:
            del self.clips[cid]
        for t in self.stops:
            self.stops[t] = [s for s in self.stops[t] if s > now - EPS]
        for t, tags in self.stop_sections.items():
            for b in [b for b in tags if b <= now - EPS]:
                del tags[b]

    def events(self, clip, b0, b1, groups):
        """Onsets of `clip` in [b0, b1): yields (cycle, event index, onset beat). groups: the clip's events as
        lists of note indices (one note each, or a mono phrase), their onset = first note."""
        if clip.length <= 0:
            return
        end = clip.end
        lo = max(b0, clip.start)
        hi = b1 if end is None else min(b1, end)
        if hi <= lo:
            return
        k0 = max(0, int(math.floor((lo - clip.start) / clip.length)))
        k1 = int(math.ceil((hi - clip.start) / clip.length))
        if getattr(clip, '_ons_for', None) is not groups:     # groups are in onset order
            clip._ons = [g.on if hasattr(g, 'on') else clip.notes[g[0]][0] for g in groups]
            clip._ons_for = groups
        ons = clip._ons
        for k in range(k0, k1 + 1):
            base = clip.start + k * clip.length
            if clip.loop is not None and k >= clip.loop:
                break
            for ei in range(bisect.bisect_left(ons, lo - EPS - base), len(ons)):
                on = base + ons[ei]
                if on >= hi - EPS:
                    break
                yield k, ei, on

    def last_change(self, now):
        """Beat of the last scheduled start/end after `now` (None if nothing is scheduled to change)."""
        pts = []
        for c in self.clips.values():
            if c.start > now + EPS:
                pts.append(c.start)
            if c.end is not None and c.end > now + EPS:
                pts.append(c.end)
        for ss in self.stops.values():
            pts += [s for s in ss if s > now + EPS]
        return max(pts) if pts else None
