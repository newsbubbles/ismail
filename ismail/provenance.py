"""What each track's sound is modeled on: measured from an example, designed on purpose, or not said.

Songs got better where their instruments were measured from recordings (mimic, measured kits, fitted voices) and
stayed fake where an agent hand-patched them. The skill says to measure first; this module makes the project say
it back on every project_info and render, so the step survives a context summary.

A track's model is worked out from what it plays, unless a fit or track_model recorded one:
  measured   a mimic profile, a library voice whose INFO names its measurements, a sample imported or extracted
             from a recording, a fit to a target (instrument_fit apply_to_track, track_fit apply), audio clips
  designed   a sound made on purpose (electronic music): track_model(on='designed'), or a library voice marked so
  unstated   a patch nobody said anything about: a synth or drum patch, a song's own code voice, a sample synthesized
             with sound_make
"""
import importlib
import json
import os

MEASURED, DESIGNED, UNSTATED = 'measured', 'designed', 'unstated'
_SAMPLE_FROM = ('imported from', 'avg of', 'fitted to')


def _library_info(voice):
    for cat in ('keys', 'guitar', 'strings', 'winds', 'brass', 'bass', 'drums', 'fx'):
        try:
            return getattr(importlib.import_module(f'ismail.voices.{cat}.{voice}'), 'INFO', None)
        except ImportError:
            continue
    return None


def _mimic_source(root, profile):
    from . import voices
    for _, d in voices.search_path(root):       # every family folder, so winds and brass profiles are found too
        p = os.path.join(d, f'{profile}.mimic.json') if d else ''
        if p and os.path.exists(p):
            try:
                with open(p, encoding='utf8') as f:
                    return json.load(f).get('source') or ''
            except (OSError, ValueError):
                return ''
    return ''


def of_instrument(inst, d, root):
    """-> (status, text) for one instrument dict (a kit piece counts too)."""
    t = inst.get('type')
    if t == 'mimic':
        src = _mimic_source(root, inst.get('profile', ''))
        return MEASURED, f"mimic profile {inst.get('profile')}" + (f" ({src})" if src else '')
    if t == 'code':
        v = inst.get('voice')
        local = v and os.path.exists(os.path.join(root, 'voices', f'{v}.py'))
        info = None if (local or not v) else _library_info(v)
        if info and info.get('measured'):
            return MEASURED, f"library voice {v}: {info['measured']}"
        if info and info.get('designed'):
            return DESIGNED, f"library voice {v}: {info['designed']}"
        return UNSTATED, f"code voice {v or '(inline code)'}" + (" (the song's own)" if local else '')
    if t == 'sampler':
        s = (d.get('sounds') or {}).get(inst.get('sound'), {})
        note = s.get('note') or ''
        if note.startswith(_SAMPLE_FROM):
            return MEASURED, f"sample {inst.get('sound')}, {note}"
        return UNSTATED, f"sample {inst.get('sound')}" + (f", {note}" if note else '')
    if t == 'kit':
        parts = [of_instrument(p, d, root) for p in (inst.get('map') or {}).values()]
        n = {k: sum(1 for s, _ in parts if s == k) for k in (MEASURED, DESIGNED, UNSTATED)}
        status = UNSTATED if n[UNSTATED] else (MEASURED if n[MEASURED] else DESIGNED)
        return status, f"kit of {len(parts)}: " + ', '.join(f"{v} {k}" for k, v in n.items() if v)
    return UNSTATED, f"{t} patch"


def of_track(tr, d, root):
    """-> (status, text). A recorded model (from a fit or track_model) wins over what the instrument says."""
    m = tr.get('model')
    if m:
        if m.get('on') == 'designed':
            return DESIGNED, 'designed' + (f" ({m['by']})" if m.get('by') else '')
        on = m['on'] if isinstance(m['on'], str) else ' + '.join(m['on'])
        return MEASURED, f"modeled on {on}" + (f" by {m['by']}" if m.get('by') else '')
    inst = tr.get('instrument')
    if not inst:
        return (MEASURED, f"{len(tr['audio'])} audio clips") if tr.get('audio') else (DESIGNED, 'empty audio track')
    return of_instrument(inst, d, root)


NEXT = ("model an acoustic or electric part on a measured example (mimic_measure; sound_extract or analyze_kit then "
        "instrument_fit with apply_to_track; track_fit apply=True; a sampler of an imported recording), or say it "
        "is a designed sound: track_model(track, on='designed')")


def summary(d, root, tracks=None):
    """-> (lines for project_info, one line for render or '' when nothing is unstated)."""
    rows = [(name, *of_track(tr, d, root)) for name, tr in d['tracks'].items() if not tracks or name in tracks]
    if not rows:
        return [], ''
    count = {k: sum(1 for _, s, _ in rows if s == k) for k in (MEASURED, DESIGNED, UNSTATED)}
    L = ["models (what each sound is modeled on): " + ', '.join(f"{v} {k}" for k, v in count.items() if v)]
    L += [f"  {name:<14} {s:<9} {text}" for name, s, text in rows]
    open_ = [name for name, s, _ in rows if s == UNSTATED]
    if open_:
        L.append(f"  unstated: {NEXT}")
    short = (f"  models: {', '.join(open_)} unstated (modeled on nothing measured); project_info lists them and the "
             f"ops that measure") if open_ else ''
    return L, short
