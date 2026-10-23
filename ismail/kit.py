"""The kit page (ledger M295): every public voice and effect by family, with what it is modeled on, its ear state and
the exam that judged it, its range, its cost and the gaps still open in its family.

The data is the product and the pages are views of it. The sources are read each time, so nothing here goes stale on
its own:
  - ismail/voices/showcase.json (voices, ranges, its own gaps) and ismail/presets.py (PRESETS),
  - each voice module's INFO and each *.mimic.json profile under ismail/voices/,
  - ismail/fx.py FX_DEFAULTS + AUTOMATABLE (rig.py and spatial.py join it), the live engine's PROCS (live blocks),
  - ismail/samples.py SETS (download size and licence of a voice's samples).
What the sources cannot say is hand-kept in ismail/kit_tables.json (the Sound Engineer gardens it): a row per public
voice (family, type, modeled on, ear state with its exam), a row per effect type, and the open gaps per family. A
voice or effect the tables do not know lands in family "unsorted", and a table row nothing in the sources plays is
listed as stale; tests/test_kit.py fails on either.

  python -m ismail.kit            regenerate docs/KIT.md and docs/kit.json
  python -m ismail.kit --check    exit 1 if either is stale (CI runs it through tests/test_kit.py)

Render cost stays "not measured" unless a voice's INFO gives a static note: probe renders are not run here.
"""
import importlib
import json
import os
import re
import sys

PKG = os.path.dirname(os.path.abspath(__file__))
VOICES = os.path.join(PKG, 'voices')
TABLES = os.path.join(PKG, 'kit_tables.json')
DOCS = os.path.join(os.path.dirname(PKG), 'docs')
OUT_JSON, OUT_MD = 'kit.json', 'KIT.md'
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def tables():
    with open(TABLES, encoding='utf8') as f:
        return json.load(f)


def _read_json(p):
    with open(p, encoding='utf8') as f:
        return json.load(f)


def _midi_name(m):
    m = int(round(m))
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def _voice_modules():
    """{name: family folder} for every built-in voice module (.py, not private)."""
    out = {}
    for fam in sorted(os.listdir(VOICES)):
        d = os.path.join(VOICES, fam)
        if os.path.isdir(d) and not fam.startswith(('_', '.')):
            out.update({f[:-3]: fam for f in sorted(os.listdir(d)) if f.endswith('.py') and not f.startswith('_')})
    return out


def _mimic_profiles():
    out = {}
    for fam in sorted(os.listdir(VOICES)):
        d = os.path.join(VOICES, fam)
        if os.path.isdir(d):
            out.update({f[:-len('.mimic.json')]: os.path.join(d, f) for f in sorted(os.listdir(d))
                        if f.endswith('.mimic.json')})
    return out


def _module_info(fam, name):
    try:
        mod = importlib.import_module(f'ismail.voices.{fam}.{name}')
    except Exception as e:                                    # a voice that fails to import is said, not hidden
        return {'_error': f'{type(e).__name__}: {e}'}
    return dict(getattr(mod, 'INFO', None) or {})


def _live_blocks():
    """Effect types with a native live-engine block, read from the live PROCS dicts (source text, no import)."""
    names = set()
    for f in ('fx_blocks.py', 'rig_blocks.py', 'spatial_blocks.py'):
        p = os.path.join(PKG, 'live', f)
        if os.path.isfile(p):
            with open(p, encoding='utf8') as fh:
                src = fh.read()
            for m in re.finditer(r'^PROCS\s*=\s*\{(.*?)\}', src, re.S | re.M):
                names |= set(re.findall(r"'(\w+)'\s*:", m.group(1)))
    return names


def _generic(text, words):
    for k, v in words.items():
        text = text.replace(k, v)
    return text


def build_voices(T):
    """(voice rows, names the tables do not know, table rows nothing plays)."""
    from . import presets as presets_mod, samples
    presets = presets_mod.PRESETS
    sc = _read_json(os.path.join(VOICES, 'showcase.json'))
    showcase = {v['name']: v for v in sc['voices']}
    modules, profiles = _voice_modules(), _mimic_profiles()
    sets = {s['voice']: (name, s) for name, s in samples.SETS.items() if 'voice' in s}
    rows_t, states = T['voices'], T['ear_states']

    # every playable public name: presets, showcase names, voice modules no preset covers, mimic profiles
    names = list(presets)
    names += [n for n in showcase if n not in names]
    covered = {p.get('voice') for p in presets.values() if p.get('type') == 'code'}
    covered |= {(v['instrument'] or {}).get('voice') for v in showcase.values() if isinstance(v['instrument'], dict)}
    names += [n for n in modules if n not in names and n not in covered]
    names += [n for n in profiles if n not in names]
    infos = {}

    def info_of(voice):
        if voice not in infos:
            infos[voice] = _module_info(modules[voice], voice) if voice in modules else {}
        return infos[voice]

    out, unsorted = [], []
    for n in names:
        p = presets.get(n) or {}
        if n in showcase:
            use = showcase[n]['instrument']
        elif n in presets:
            use = f'preset:{n}'
        elif n in profiles:
            use = {'type': 'mimic', 'profile': n}
        else:
            use = {'type': 'code', 'voice': n}
        voice = (use.get('voice') if isinstance(use, dict) else None) or p.get('voice') or (n if n in modules else None)
        info = info_of(voice) if voice else {}
        t = rows_t.get(n)
        if t is None:
            unsorted.append(n)
            t = {'family': 'unsorted', 'type': 'mimic' if n in profiles else 'sprite' if p else 'designed',
                 'engine': '?', 'modeled_on': '?', 'ear': 'untested', 'exam': None}
        ear = t.get('ear', 'untested')
        if ear not in states:
            raise ValueError(f"kit_tables.json: voice {n!r} has ear {ear!r}; one of {', '.join(states)}")
        heard = t.get('heard', '')
        if t['type'] == 'sprite' and not heard and n not in showcase:
            heard = T['never_first']
        # range: the table, then showcase, then a mimic profile's measured notes, then the voice's INFO, then by kind
        rng, src = t.get('range'), 'kit_tables.json'
        if not rng and (showcase.get(n) or {}).get('range'):
            rng, src = showcase[n]['range'], 'showcase.json'
        if not rng and n in profiles:
            ms = [x['midi'] for x in _read_json(profiles[n]).get('notes', []) if 'midi' in x]
            if ms:
                rng, src = f"{_midi_name(min(ms))}-{_midi_name(max(ms))} (measured notes)", 'mimic profile'
        if not rng and info.get('range'):
            rng, src = str(info['range']), 'voice INFO'
        if not rng:
            k = p.get('type')
            rng = ('any pitch (synth)' if k == 'synth' else 'GM drum map' if k == 'kit'
                   else 'one hit, pitch ignored' if k in ('kick', 'snare', 'clap', 'hat') else 'unknown')
            src = 'by instrument type'
        cost = 'not measured'
        if info.get('render'):
            cost = f"not measured; INFO says: {info['render'].split('. ')[0]}"
        need = None
        if voice in sets:
            set_name, s = sets[voice]
            need = {'set': set_name, 'size_mb': s['size_mb'], 'licence': s['licence'], 'credit': s.get('credit')}
        source = None
        if n in profiles:
            source = _read_json(profiles[n]).get('source', '')
            if len(source) > 160:                              # a list of file names, not a credit line
                source = source[:120].rsplit(',', 1)[0] + ', ... (file list)'
        elif info.get('source') and info['source'] != 'designed':
            source = info['source']
        sv = showcase.get(n) or {}
        out.append({
            'name': n, 'family': t['family'], 'type': t['type'], 'engine': t.get('engine', '?'),
            'modeled_on': t['modeled_on'], 'ear': ear, 'exam': t.get('exam'), 'heard': heard,
            'range': rng, 'range_from': src, 'cost': cost, 'needs': need,
            'showcase': bool(sv), 'standin': sv.get('standin'), 'roles': sv.get('roles', []),
            'use': use, 'source': source,
            'functions': sorted((info.get('functions') or {}).keys()) or None,
            'lanes': sorted((info.get('lanes') or {}).keys()) or None,
            'info_error': info.get('_error'),
        })
    stale = sorted(set(rows_t) - set(names))
    return out, unsorted, stale, sc


def build_effects(T):
    from . import fx
    live = _live_blocks()
    stacks = _read_json(os.path.join(VOICES, 'fx_stacks.json')).get('stacks', {})
    out, unsorted = [], []
    for t, defaults in fx.FX_DEFAULTS.items():
        row = T['effects'].get(t)
        if row is None:
            unsorted.append(t)
            row = {'family': 'unsorted', 'what': '?'}
        out.append({'name': t, 'family': row['family'], 'what': row['what'], 'params': sorted(defaults),
                    'automatable': list(fx.AUTOMATABLE.get(t, ())),
                    'live': 'native block' if t in live else 'render only'})
    chains = [{'name': k, 'effects': [e['type'] for e in v.get('chain', [])], 'notes': v.get('notes', '')}
              for k, v in stacks.items()]
    stale = sorted(set(T['effects']) - set(fx.FX_DEFAULTS))
    return out, unsorted, stale, chains


def build():
    """The kit as data: the dict docs/kit.json holds. Deterministic (no clock), so --check can compare."""
    T = tables()
    voices, vu, vstale, sc = build_voices(T)
    effects, eu, estale, chains = build_effects(T)
    fams = {}
    for v in voices:
        d = fams.setdefault(v['family'], {s: 0 for s in T['ear_states']})
        d[v['ear']] += 1
    efams = {f: sum(1 for e in effects if e['family'] == f)
             for f in _ordered({e['family'] for e in effects}, T['fx_families'])}
    return {
        'about': 'ismail public kit by family (ledger M295). Generated by python -m ismail.kit from the sources and '
                 'ismail/kit_tables.json; edit those and rerun, never this file.',
        'families': {'voices': T['voice_families'], 'effects': T['fx_families']},
        'types': T['types'], 'ear_states': T['ear_states'],
        'summary': {'voices': len(voices), 'voices_with_ear_state': sum(1 for v in voices if v['ear'] != 'untested'),
                    'by_family': fams, 'effects': len(effects), 'effects_by_family': efams},
        'voices': voices, 'effects': effects, 'fx_chains': chains,
        'gaps': T['gaps'], 'fx_gaps': T['fx_gaps'],
        'showcase_gaps': [_generic(g, T['generic_words']) for g in sc.get('gaps', [])],
        'unsorted': {'voices': vu, 'effects': eu},
        'stale_rows': {'voices': vstale, 'effects': estale},
    }


def _ordered(names, order):
    return [f for f in order if f in names] + sorted(f for f in names if f not in order)


def _cell(s):
    return str(s if s is not None else '').replace('|', '/').replace('\n', ' ')


def render_md(data):
    T = tables()
    s, never = data['summary'], T['never_first']
    L = ['# The ismail kit', '',
         'Every public voice and effect by family: what it is modeled on, how it did in front of the ear and which '
         'exam judged it, its range, its cost, and the gaps still open in each family.', '',
         'Generated by `python -m ismail.kit` from showcase.json, presets.py, each voice\'s INFO and mimic profile, '
         'fx.py and the live engine, plus the hand-kept rows in `ismail/kit_tables.json`. Do not edit this page: '
         'change the sources or the tables and rerun. The same data is in [kit.json](kit.json); agents ask the '
         '`kit` op.', '',
         f"{s['voices']} public voices, {s['voices_with_ear_state']} with a known ear state; {s['effects']} effects.",
         '', '## How to read it', '',
         '- **type**: ' + '; '.join(f'{k}: {v}' for k, v in data['types'].items()) + '.',
         '- **ear**: ' + '; '.join(f'{k}: {v}' for k, v in data['ear_states'].items()) + '. Each state names the '
         'exam or ledger row that judged it.',
         '- **modeled on**: an instrument type, never a maker or a model name.',
         '- **cost**: "not measured" until a render-time probe runs on a quiet machine; a voice\'s own note on its '
         'render time is quoted. A voice with a download plays samples fetched on the person\'s word (size given).',
         '- A showcase voice (marked *) is one a first sketch reaches for.', '', '## Voices by family', '']
    by = {}
    for v in data['voices']:
        by.setdefault(v['family'], []).append(v)
    for fam in _ordered(set(by) | set(data['families']['voices']), data['families']['voices']):
        vs = by.get(fam, [])
        L += [f'### {fam}', '']
        if not vs:
            L += ['Nothing public yet.', '']
        else:
            L += ['| voice | type | modeled on | ear | judged by | range | cost |', '|---|---|---|---|---|---|---|']
            for v in vs:
                ear = v['ear'] + (f": {v['heard']}" if v['heard'] and v['ear'] != 'untested' else '')
                cost = v['cost'] + (f"; download {v['needs']['size_mb']} MB" if v['needs'] else '')
                L.append(f"| {v['name']}{' *' if v['showcase'] else ''} | {v['type']} | {_cell(v['modeled_on'])} "
                         f"| {_cell(ear)} | {_cell(v['exam'] or '-')} | {_cell(v['range'])} | {_cell(cost)} |")
            L.append('')
            notes = [v for v in vs if v['ear'] == 'untested' and v['heard'] and v['heard'] != never]
            L += [f"- {v['name']}: {v['heard']}" for v in notes]
            sprites = [v['name'] for v in vs if v['heard'] == never]
            if sprites:
                L.append(f"- {', '.join(sprites)}: {never}.")
            if notes or sprites:
                L.append('')
        gs = [g for g in data['gaps'] if g['family'] == fam]
        if gs:
            L += ['Open gaps:', '']
            L += [f"- {g['gap']} ({g['cite']}). Under way: {g['under_way']}." for g in gs]
            L.append('')
    L += ['## Effects by family', '']
    eby = {}
    for e in data['effects']:
        eby.setdefault(e['family'], []).append(e)
    for fam in _ordered(set(eby) | set(data['families']['effects']), data['families']['effects']):
        es = eby.get(fam, [])
        L += [f'### {fam}', '']
        if es:
            L += ['| effect | what it does | automatable | live |', '|---|---|---|---|']
            L += [f"| {e['name']} | {_cell(e['what'])} | {', '.join(e['automatable']) or '-'} | {e['live']} |"
                  for e in es]
            L.append('')
        gs = [g for g in data['fx_gaps'] if g['family'] == fam]
        if gs:
            L += ['Open gaps:', '']
            L += [f"- {g['gap']} ({g['cite']}). Now: {g['have_now']}." for g in gs]
            L.append('')
    if data['fx_chains']:
        L += ['### saved chains', '', 'Built-in effect stacks (`fx_stack_list`):', '']
        L += [f"- {c['name']}: {' > '.join(c['effects'])}" for c in data['fx_chains']]
        L.append('')
    L += ['## Gaps the showcase itself lists', '']
    L += [f'- {g}' for g in data['showcase_gaps']] + ['']
    u, st = data['unsorted'], data['stale_rows']
    if any(u.values()) or any(st.values()):
        L += ['## Not yet sorted (add or remove a row in ismail/kit_tables.json)', '',
              f"- unsorted voices: {', '.join(u['voices']) or 'none'}; effects: {', '.join(u['effects']) or 'none'}",
              f"- rows nothing plays: voices {', '.join(st['voices']) or 'none'}; "
              f"effects {', '.join(st['effects']) or 'none'}", '']
    return '\n'.join(L).rstrip('\n') + '\n'


def outputs(data=None):
    """{file name: text} for the docs folder."""
    data = data or build()
    return {OUT_JSON: json.dumps(data, indent=1, ensure_ascii=False) + '\n', OUT_MD: render_md(data)}


def stale(out_dir=DOCS, data=None):
    """The file names in out_dir that differ from a fresh build (missing counts as stale)."""
    bad = []
    for name, text in outputs(data).items():
        try:
            with open(os.path.join(out_dir, name), encoding='utf8', newline='') as f:
                have = f.read().replace('\r\n', '\n')
        except OSError:
            have = None
        if have != text:
            bad.append(name)
    return bad


def write(out_dir=DOCS, data=None):
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for name, text in outputs(data).items():
        p = os.path.join(out_dir, name)
        with open(p, 'w', encoding='utf8', newline='\n') as f:
            f.write(text)
        paths.append(p)
    return paths


# ---------------------------------------------------------------- the op's text

ALIASES = {'strings': 'bowed strings', 'winds': 'woodwinds', 'kits': 'drum kits', 'drums': 'drum kits',
           'analog': 'analog synths', 'drum machine': 'drum machines', 'drum-machine': 'drum machines'}
SYNTHS = ('analog synths', 'drum machines', 'FM', 'wavetable', 'granular')


def _families(data, family):
    """The voice and effect families one asks for: a family name, an alias, 'synths', 'effects' or 'gaps'."""
    vf, ef = data['families']['voices'], data['families']['effects']
    f = (family or '').strip()
    low = {x.lower(): x for x in vf + ef}
    if f.lower() == 'synths':
        return list(SYNTHS), []
    if f.lower() in ('effects', 'fx effects'):
        return [], list(ef)
    name = low.get(ALIASES.get(f.lower(), f).lower())
    if name is None:
        return None, None
    return ([name], []) if name in vf else ([], [name])


def text(family=None, voice=None, data=None):
    """The kit as text for agents: every family's voices, one family (or its effects), or one voice in full."""
    data = data or build()
    if voice:
        v = next((x for x in data['voices'] if x['name'] == voice), None)
        if v is None:
            e = next((x for x in data['effects'] if x['name'] == voice), None)
            if e is None:
                return None
            return '\n'.join([f"{e['name']} (effect, {e['family']}): {e['what']}",
                              f"  params: {', '.join(e['params'])}",
                              f"  automatable: {', '.join(e['automatable']) or '-'}; live: {e['live']}"])
        L = [f"{v['name']} ({v['family']}, {v['type']}{', showcase' if v['showcase'] else ''}): {v['modeled_on']}",
             f"  ear: {v['ear']}" + (f" ({v['exam']})" if v['exam'] else '')
             + (f": {v['heard']}" if v['heard'] else ''),
             f"  range: {v['range']} (from {v['range_from']})",
             f"  engine: {v['engine']}; cost: {v['cost']}",
             f"  use: instrument={json.dumps(v['use']) if isinstance(v['use'], dict) else repr(v['use'])}"]
        if v['needs']:
            n = v['needs']
            L.append(f"  needs: sample set {n['set']} ({n['size_mb']} MB, {n['licence']}); samples_fetch on the "
                     f"person's word")
        if v['functions']:
            L.append(f"  functions: {', '.join(v['functions'])}")
        if v['lanes']:
            L.append(f"  lanes: {', '.join(v['lanes'])}")
        if v['source']:
            L.append(f"  source: {v['source']}")
        gs = [g for g in data['gaps'] if g['family'] == v['family']]
        L += [f"  gap in {v['family']}: {g['gap']} ({g['cite']})" for g in gs]
        return '\n'.join(L)
    vfams, efams = (data['families']['voices'], []) if not family else _families(data, family)
    if vfams is None:
        return None
    L = []
    for fam in vfams:
        vs = [v for v in data['voices'] if v['family'] == fam]
        L.append(f"{fam}: " + (f"{len(vs)} voice{'s' if len(vs) != 1 else ''}" if vs else 'nothing public yet'))
        for v in vs:
            exam = f" ({v['exam']})" if v['exam'] else ''
            L.append(f"  {v['name']:<16} {v['type']:<9} ear {v['ear']}{exam}; range {v['range']}; "
                     f"modeled on {v['modeled_on']}; cost {v['cost']}")
        if family:
            L += [f"  gap: {g['gap']} ({g['cite']}); under way: {g['under_way']}"
                  for g in data['gaps'] if g['family'] == fam]
    for fam in efams:
        es = [e for e in data['effects'] if e['family'] == fam]
        L.append(f"{fam} (effects): " + ', '.join(e['name'] for e in es))
        if family:
            L += [f"  {e['name']:<12} {e['what']}; live: {e['live']}" for e in es]
            L += [f"  gap: {g['gap']} ({g['cite']}); now: {g['have_now']}" for g in data['fx_gaps']
                  if g['family'] == fam]
    if not family:
        L.append('effects: ' + '; '.join(f"{f} {n}" for f, n in data['summary']['effects_by_family'].items())
                 + " (kit(family='<effect family>') lists them)")
        u = data['unsorted']['voices']
        if u:
            L.append(f"unsorted (no row in ismail/kit_tables.json yet): {', '.join(u)}")
    return '\n'.join(L)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out_dir = DOCS
    if '--out' in argv:
        out_dir = argv[argv.index('--out') + 1]
    data = build()
    if '--check' in argv:
        bad = stale(out_dir, data)
        if bad:
            print(f"stale: {', '.join(bad)} in {out_dir}; run python -m ismail.kit and commit them")
            return 1
        print(f'{OUT_MD} and {OUT_JSON} are current')
        return 0
    for p in write(out_dir, data):
        print(f'wrote {p}')
    s = data['summary']
    print(f"{s['voices']} voices ({s['voices_with_ear_state']} with an ear state), {s['effects']} effects")
    for fam in _ordered(set(s['by_family']) | set(data['families']['voices']), data['families']['voices']):
        d = s['by_family'].get(fam, {})
        print(f"  {fam:<16} {sum(d.values()):>2}  " + ' '.join(f'{k} {v}' for k, v in d.items() if v))
    print('  effects: ' + ', '.join(f'{k} {v}' for k, v in s['effects_by_family'].items()))
    u, st = data['unsorted'], data['stale_rows']
    if any(u.values()):
        print(f"UNSORTED (add rows to ismail/kit_tables.json): voices {u['voices']}, effects {u['effects']}")
    if any(st.values()):
        print(f"STALE ROWS (nothing plays them): voices {st['voices']}, effects {st['effects']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
