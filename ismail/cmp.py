"""Stored, queryable comparisons between A (your render) and B (the reference), per stem, down to grid steps.

A report is built once (features of every stem on both sides) and saved under <project>/comparisons/<id>/:
  report.json   per-stem per-bar metrics, summary, baselines, sections of B
  A_<stem>.npz  A's step features (B's are cached by content)
Views then page / drill into it without recomputing: summary -> sections -> bars -> zoom (steps).

Metrics (per bar, per stem). All are computed on the grid, so they punish wrong timing as well as wrong content:
  dlevel      level difference A-B (dB)
  dbands      6-band difference A-B (dB)
  note_f1     F1 of sounding notes per step (exact pitch)       - harmony + voicing + note lengths
  pc_f1       same on pitch classes (octave-agnostic)
  attack_f1   F1 of note starts (+-1 step, exact pitch)         - the rhythm of pitched parts
  hit_f1      F1 of drum-lane hits (+-1 step), per lane          - drum pattern
  shape       correlation of the per-step level curve           - pumping, gating, accents
"""
import json
import os
import time

import numpy as np

from . import features as FE
from . import structure as ST
from .notation import midi_to_name

LANES = FE.LANES


def _f1(tp, fp, fn):
    if tp + fp + fn == 0:
        return None  # nothing on either side: not informative
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def _tol(x, tol=1):
    """Dilate a boolean (steps, k) array along time by +-tol."""
    out = x.copy()
    for d in range(1, tol + 1):
        out[d:] |= x[:-d]
        out[:-d] |= x[d:]
    return out


def _pc(sets):
    n = sets.shape[0]
    out = np.zeros((n, 12), bool)
    for p in range(sets.shape[1]):
        out[:, (p + FE.PITCH_BASE) % 12] |= sets[:, p]
    return out


def bar_metrics(FA, FB, pitched=True, drums=True):
    """Per-bar metric rows for aligned feature dicts (same grid). Returns list of dicts."""
    spb = FE.steps_per_bar(FB)
    n = min(len(FA['level']), len(FB['level']))
    FA = {k: (v if k == 'meta' else v[:n]) for k, v in FA.items()}
    FB = {k: (v if k == 'meta' else v[:n]) for k, v in FB.items()}
    nb = n // spb
    SA, SB = FE.note_sets(FA)[:n], FE.note_sets(FB)[:n]
    AA, AB = FE.note_attacks(FA, SA)[:n], FE.note_attacks(FB, SB)[:n]
    PA, PB = _pc(SA), _pc(SB)
    HA, HB = FE.hit_sets(FA)[:n], FE.hit_sets(FB)[:n]
    AAt, ABt = _tol(AA), _tol(AB)
    HAt, HBt = _tol(HA), _tol(HB)
    la_all = ST.bar_levels({k: v[:n] for k, v in FA.items() if k != 'meta'} | {'meta': FA['meta']})
    lb_all = ST.bar_levels({k: v[:n] for k, v in FB.items() if k != 'meta'} | {'meta': FB['meta']})
    topb = np.max(lb_all)
    topa = np.max(la_all)
    # common floor: 60 dB under the pair's loudest bar, so silence vs leakage can't produce 100 dB errors
    floor = max(topa, topb) - 36  # the arrangement map's 'silent' line
    la_all = np.maximum(la_all, floor)
    lb_all = np.maximum(lb_all, floor)
    bfloor = max(FA['bands'].max(), FB['bands'].max()) - 70
    rows = []
    for b in range(nb):
        sl = slice(b * spb, (b + 1) * spb)
        r = {'bar': b + 1, 'level_a': float(la_all[b]), 'level_b': float(lb_all[b])}
        r['dlevel'] = r['level_a'] - r['level_b']
        ba = np.maximum(10 * np.log10(np.mean(10 ** (FA['bands'][sl] / 10), 0) + 1e-12), bfloor)
        bb = np.maximum(10 * np.log10(np.mean(10 ** (FB['bands'][sl] / 10), 0) + 1e-12), bfloor)
        r['dbands'] = (ba - bb).round(2).tolist()
        r['active_a'] = bool(la_all[b] > topa - 30)
        r['active_b'] = bool(lb_all[b] > topb - 30)
        if pitched:
            tp = int((SA[sl] & SB[sl]).sum())
            r['note_f1'] = _f1(tp, int((SA[sl] & ~SB[sl]).sum()), int((~SA[sl] & SB[sl]).sum()))
            tp = int((PA[sl] & PB[sl]).sum())
            r['pc_f1'] = _f1(tp, int((PA[sl] & ~PB[sl]).sum()), int((~PA[sl] & PB[sl]).sum()))
            tp_a = int((AA[sl] & ABt[sl]).sum())
            tp_b = int((AB[sl] & AAt[sl]).sum())
            r['attack_f1'] = _f1((tp_a + tp_b) / 2, int((AA[sl] & ~ABt[sl]).sum()), int((AB[sl] & ~AAt[sl]).sum()))
            r['n_notes_a'] = int(SA[sl].sum())
            r['n_notes_b'] = int(SB[sl].sum())
            r['n_attacks_a'] = int(AA[sl].sum())
            r['n_attacks_b'] = int(AB[sl].sum())
            # messiness: how much of YOUR attack loudness starts notes the reference doesn't start, and vice versa
            wa = 10 ** (FA['sal'][sl] / 20)
            wb = 10 ** (FB['sal'][sl] / 20)
            ta, tb = float((wa * AA[sl]).sum()), float((wb * AB[sl]).sum())
            r['extra_share'] = float((wa * (AA[sl] & ~ABt[sl])).sum() / ta) if ta > 0 else None
            r['missed_share'] = float((wb * (AB[sl] & ~AAt[sl])).sum() / tb) if tb > 0 else None
            r['attack_p'] = float((AA[sl] & ABt[sl]).sum() / AA[sl].sum()) if AA[sl].sum() else None
            r['attack_r'] = float((AB[sl] & AAt[sl]).sum() / AB[sl].sum()) if AB[sl].sum() else None
        if drums:
            hf = {}
            for li, lane in enumerate(LANES):
                ta = int((HA[sl, li] & HBt[sl, li]).sum())
                tb = int((HB[sl, li] & HAt[sl, li]).sum())
                hf[lane] = _f1((ta + tb) / 2, int((HA[sl, li] & ~HBt[sl, li]).sum()), int((HB[sl, li] & ~HAt[sl, li]).sum()))
            r['hit_f1'] = hf
        top = lambda x: float(np.mean(np.sort(x)[-4:]))  # noqa: E731  loudest attacks in the bar
        # transient sharpness is only meaningful for pitched material: synthetic drum hits vs bleed-masked
        # separated drums measures masking, not attack hardness
        if pitched and 'flux' in FA and 'flux' in FB and r['active_a'] and r['active_b']:
            r['transient_a'], r['transient_b'] = top(FA['flux'][sl]), top(FB['flux'][sl])
            r['transient_err'] = abs(r['transient_a'] - r['transient_b'])
        xa, xb = FA['level'][sl], FB['level'][sl]
        r['shape'] = float(np.corrcoef(xa, xb)[0, 1]) if xa.std() > 0.5 and xb.std() > 0.5 else None
        r['width_a'] = float(np.mean(FA['width'][sl]))
        r['width_b'] = float(np.mean(FB['width'][sl]))
        r['centroid_a'] = float(np.median(FA['centroid'][sl]))
        r['centroid_b'] = float(np.median(FB['centroid'][sl]))
        rows.append(r)
    return rows


def _mean(vals):
    v = [x for x in vals if x is not None]
    return float(np.mean(v)) if v else None


def summarize(rows, bars=None):
    sel = [r for r in rows if (bars is None or bars[0] <= r['bar'] <= bars[1]) and (r['active_a'] or r['active_b'])]
    if not sel:
        return {}
    s = {'bars': len(sel),
         'level_err': float(np.mean([abs(r['dlevel']) for r in sel])),
         'level_bias': float(np.mean([r['dlevel'] for r in sel])),
         'band_err': float(np.mean([np.mean(np.abs(r['dbands'])) for r in sel])),
         'band_bias': np.round(np.mean([r['dbands'] for r in sel], 0), 1).tolist(),
         'shape': _mean([r['shape'] for r in sel]),
         'transient_err': _mean([r.get('transient_err') for r in sel]),
         'perceptual': _mean([r.get('perceptual') for r in sel]),
         'transient_bias': _mean([(r['transient_a'] - r['transient_b']) if 'transient_a' in r else None for r in sel]),
         'width_a': float(np.mean([r['width_a'] for r in sel])), 'width_b': float(np.mean([r['width_b'] for r in sel])),
         'presence_agree': float(np.mean([r['active_a'] == r['active_b'] for r in rows]))}
    if 'note_f1' in sel[0]:
        for k in ('note_f1', 'pc_f1', 'attack_f1', 'attack_p', 'attack_r', 'extra_share', 'missed_share'):
            s[k] = _mean([r[k] for r in sel])
    if 'hit_f1' in sel[0]:
        for lane in LANES:
            s['hit_' + lane] = _mean([r['hit_f1'][lane] for r in sel])
    return s


# ------------------------------------------------------------------ baselines from B alone

def _shift(F, steps):
    out = {}
    for k, v in F.items():
        out[k] = v if k == 'meta' else np.roll(v, steps, axis=0)
    return out


def baselines(FB, pitched, drums, loop_bars=8):
    """B against itself shifted by one loop length (same material, other place: a realistic ceiling) and by
    half a loop (right sounds, wrong harmony/pattern position: the 'plausible but wrong' floor)."""
    spb = FE.steps_per_bar(FB)
    out = {}
    for name, sh in (('self_loop', loop_bars * spb), ('half_loop', loop_bars // 2 * spb), ('one_beat', spb // 4)):
        rows = bar_metrics(_shift(FB, sh), FB, pitched, drums)
        rows = rows[loop_bars:-loop_bars] if len(rows) > 3 * loop_bars else rows
        out[name] = summarize(rows)
    return out


# ------------------------------------------------------------------ report

STEM_KIND = {'mix': (True, True), 'drums': (False, True), 'bass': (True, False), 'other': (True, False),
             'vocals': (True, False)}


def build(out_dir, a_srcs, b_srcs, grid_a, grid_b, cache_dir, label='', loop_bars=8, extra_meta=None, perceptual=True):
    os.makedirs(out_dir, exist_ok=True)
    rep = {'created': time.time(), 'label': label, 'a': a_srcs, 'b': b_srcs,
           'grid_a': [grid_a.bpm, grid_a.offset, grid_a.bpb], 'grid_b': [grid_b.bpm, grid_b.offset, grid_b.bpb],
           'stems': {}, 'meta': extra_meta or {}}
    FB_mix = None
    mix_top = None
    fb_stems = {}
    rep['skipped'] = {}
    for stem in [s for s in ('mix', 'drums', 'bass', 'other', 'vocals') if s in a_srcs and s in b_srcs]:
        FB = FE.extract(b_srcs[stem], grid_b, cache_dir=cache_dir)
        top = float(np.percentile(ST.bar_levels(FB), 90))
        if stem == 'mix':
            mix_top = top
        elif mix_top is not None and top < mix_top - 20:
            rep['skipped'][stem] = (f"reference {stem} stem peaks {mix_top - top:.0f} dB under the mix: separation left it "
                                    f"(nearly) empty, its content lives in another stem; not scored")
            continue
        FA = FE.extract(a_srcs[stem], grid_a, cache_dir=cache_dir)
        fb_stems[stem] = FB
        np.savez_compressed(os.path.join(out_dir, f"A_{stem}.npz"), **FA)
        pitched, drums = STEM_KIND.get(stem, (True, True))
        rows = bar_metrics(FA, FB, pitched, drums)
        perc = None
        if perceptual:
            from . import perceptual as PC
            if PC.available():
                perc = PC.compare(a_srcs[stem], b_srcs[stem], grid_b, len(rows))
                for r in rows:
                    i = min((r['bar'] - 1) // 2, len(perc['sims']) - 1)
                    r['perceptual'] = float(perc['sims'][i])
        cons_a = summarize(bar_metrics(_shift(FA, loop_bars * FE.steps_per_bar(FA)), FA, pitched, drums)[loop_bars:])
        rep['stems'][stem] = {'rows': rows, 'summary': summarize(rows), 'baselines': baselines(FB, pitched, drums, loop_bars),
                              'consistency_a': cons_a, 'perceptual': perc,
                              'pitched': pitched, 'drums': drums}
        if perc and perc.get('self_loop'):
            rep['stems'][stem]['baselines']['perc_ceiling'] = perc['self_loop']
        if stem == 'mix':
            FB_mix = FB
    if FB_mix is not None:
        segs, _ = ST.sections(FB_mix, V=ST.arrangement_vectors(FB_mix, {k: v for k, v in fb_stems.items() if k != 'mix'}))
        rep['sections_b'] = [{'label': s['label'], 'start': s['start'], 'end': s['end']} for s in segs]
    with open(os.path.join(out_dir, 'report.json'), 'w', encoding='utf8') as f:
        json.dump(rep, f)
    return rep


def load(out_dir):
    with open(os.path.join(out_dir, 'report.json'), encoding='utf8') as f:
        return json.load(f)


# ------------------------------------------------------------------ views

METRIC_ORDER = ['note_f1', 'pc_f1', 'attack_f1', 'attack_p', 'extra_share', 'hit_low', 'hit_snare', 'hit_hat', 'shape',
                'transient_err', 'band_err', 'level_err', 'perceptual']
HIGHER_BETTER = {'note_f1', 'pc_f1', 'attack_f1', 'attack_p', 'hit_low', 'hit_snare', 'hit_hat', 'shape'}
LOWER_BETTER_SHARE = {'extra_share'}


FLOOR_OF = {'hit_low': 'one_beat', 'hit_snare': 'one_beat', 'hit_hat': 'one_beat', 'shape': 'one_beat'}


GROUPS = {'notes': ('note_f1', 'pc_f1'),
          'rhythm': ('attack_f1', 'attack_p', 'hit_low', 'hit_snare', 'hit_hat'),
          'clean': ('clutter', 'consistency', 'extra_share'),
          'sound': ('band_err', 'level_err', 'transient_err', 'shape'),
          'perceptual': ('perceptual',)}
MIN_SPREAD = 0.05  # a metric whose ceiling and floor differ less than this cannot tell right from wrong: not scored
PERC_FLOOR = 0.5  # CLAP cosine typical of unrelated music; the ceiling is the reference vs itself one loop later


def closeness(s, base, cons_a=None):
    """Per metric: 0 = as good as a 'plausible but wrong' floor, 1 = as good as the reference against itself one loop
    later (capped at 1: matching the reference's own noise is not extra credit). Floors: half a loop off for pitch
    metrics, one beat off for drum hits and level shape; errors: 1 = no error, 0 = the floor's error.
    Derived cleanliness metrics: clutter (attacks sharper than the reference: 0 at +2 dB/frame) and consistency
    (your part repeating less than the reference's: 0 at a 0.15 attack-F1 deficit)."""
    out = {}
    hi = base.get('self_loop', {})
    for k in METRIC_ORDER:
        lo = base.get(FLOOR_OF.get(k, 'half_loop'), {})
        if s.get(k) is None or lo.get(k) is None:
            continue
        if k in HIGHER_BETTER:
            if hi.get(k) is None or hi[k] - lo[k] < MIN_SPREAD:
                continue
            v = (s[k] - lo[k]) / (hi[k] - lo[k])
        elif k in LOWER_BETTER_SHARE:
            if hi.get(k) is None or lo[k] - hi[k] < MIN_SPREAD:
                continue
            v = (lo[k] - s[k]) / (lo[k] - hi[k])
        else:
            v = 1 - s[k] / max(lo[k], 2.0 if k == 'transient_err' else 3.0)
        out[k] = float(np.clip(v, -1, 1.0))
    if s.get('perceptual') is not None and base.get('perc_ceiling'):
        c = base['perc_ceiling']
        out['perceptual'] = float(np.clip((s['perceptual'] - PERC_FLOOR) / max(c - PERC_FLOOR, 0.05), -1, 1))
    if s.get('transient_bias') is not None:
        out['clutter'] = float(np.clip(1 - max(0.0, s['transient_bias']) / 2.0, -1, 1))
    if cons_a and cons_a.get('attack_f1') is not None and hi.get('attack_f1') is not None:
        gap = max(0.0, hi['attack_f1'] - cons_a['attack_f1'])
        out['consistency'] = float(np.clip(1 - gap / 0.15, -1, 1))
    return out


def group_closeness(cl):
    g = {}
    for name, keys in GROUPS.items():
        v = [cl[k] for k in keys if k in cl]
        if v:
            g[name] = float(np.mean(v))
    return g, (float(np.mean(list(g.values()))) if g else float('nan'))


def _fmt(v, k):
    if v is None:
        return '   -  '
    return f"{v:6.1f}" if k in ('band_err', 'level_err', 'transient_err') else f"{v:6.2f}"


def view_summary(rep):
    L = [f"comparison '{rep.get('label', '')}' ({time.ctime(rep['created'])})  A=yours  B=reference"]
    L.append("per stem: value | ceiling (B vs itself one loop later) | floor (B vs itself half a loop off). "
             "closeness 1.0 = ceiling, 0 = floor")
    tot = []
    for stem, st in rep['stems'].items():
        s, base = st['summary'], st['baselines']
        cl = closeness(s, base, st.get('consistency_a'))
        groups, overall = group_closeness(cl)
        ks = [k for k in METRIC_ORDER if s.get(k) is not None]
        L.append(f"\n[{stem}] closeness {overall:.2f} (" + ', '.join(f"{k} {v:.2f}" for k, v in groups.items()) +
                 ")\n  " + f"level bias A-B {s.get('level_bias', 0):+.1f} dB, band bias " +
                 ' '.join(f"{n}{v:+.0f}" for n, v in zip(FE.BAND_NAMES, s.get('band_bias', []))) +
                 f", width A {s.get('width_a', 0):.2f} B {s.get('width_b', 0):.2f}, presence agree {s.get('presence_agree', 0):.2f}")
        L.append(f"  {'metric':<10} {'yours':>6} {'ceil':>6} {'floor':>6} {'close':>6}")
        for k in ks:
            ceil_v = base.get('perc_ceiling') if k == 'perceptual' else base['self_loop'].get(k)
            floor_v = PERC_FLOOR if k == 'perceptual' else base[FLOOR_OF.get(k, 'half_loop')].get(k)
            L.append(f"  {k:<10} {_fmt(s[k], k)} {_fmt(ceil_v, k)} {_fmt(floor_v, k)} "
                     f"{('%6.2f' % cl[k]) if k in cl else '   -  '}")
        if s.get('perceptual') is not None and not base.get('perc_ceiling'):
            L.append("  perceptual has no ceiling here (the song is too short to compare the reference with itself one"
                     " loop later); read the raw value: about 0.5 = unrelated music, 0.9+ = very close")
        ca, cb = st.get('consistency_a') or {}, base.get('self_loop', {})
        if ca.get('attack_f1') is not None and cb.get('attack_f1') is not None:
            L.append(f"  loop self-consistency (each vs one loop later): yours note {ca['note_f1']:.2f} attack "
                     f"{ca['attack_f1']:.2f} | reference note {cb['note_f1']:.2f} attack {cb['attack_f1']:.2f}"
                     + ("  <- yours is LESS repetitive than the reference: likely noise notes" if
                        ca['attack_f1'] < cb['attack_f1'] - 0.05 else ''))
        if s.get('transient_bias') is not None and s['transient_bias'] > 1.5:
            L.append(f"  WARNING attacks are {s['transient_bias']:+.1f} dB/frame sharper than the reference: hard, clicky or "
                     f"too many note starts (sounds cluttered even when pitches match). Soften attacks or remove notes")
        ref_extra = base.get('self_loop', {}).get('extra_share')
        if s.get('extra_share') is not None and s['extra_share'] > max(0.3, (ref_extra or 0) + 0.05):
            L.append(f"  WARNING {s['extra_share']:.0%} of your attack loudness starts notes the reference does not "
                     f"start (attack precision {s.get('attack_p', 0):.2f}): audible clutter. cmp_zoom a bar to see them")
        if cl:
            tot.append(overall)
    for stem, why in rep.get('skipped', {}).items():
        L.append(f"\n[{stem}] skipped: {why}")
    if tot:
        L.insert(2, f"OVERALL closeness (mean over scored stems): {np.mean(tot):.2f}")
    L.append("\nnext: cmp_sections / cmp_bars / cmp_worst to find where; cmp_zoom(bar) to see the steps; a part that "
             "stays wrong while these numbers look fine: zoom both on it, spectrogram(seconds=, f_lo=, f_hi=)")
    return '\n'.join(L)


def view_sections(rep, stem='mix'):
    st = rep['stems'][stem]
    L = [f"[{stem}] per reference section (B's sections)"]
    ks = [k for k in METRIC_ORDER if st['summary'].get(k) is not None]
    L.append(f"{'sect':<5}{'bars':<9}" + ''.join(f"{k[:11]:>12}" for k in ks) + f"{'lvlA-B':>8}")
    nbars = len(st['rows'])
    for sec in rep.get('sections_b', []):
        if sec['start'] > nbars:
            continue  # the reference runs longer than your project: nothing of yours to compare there
        sec = dict(sec, end=min(sec['end'], nbars))
        s = summarize(st['rows'], [sec['start'], sec['end']])
        if not s:
            L.append(f"{sec['label']:<5}{sec['start']:>3}-{sec['end']:<5} (silent on both)")
            continue
        L.append(f"{sec['label']:<5}{sec['start']:>3}-{sec['end']:<5}" + ''.join(f"{_fmt(s.get(k), k):>12}" for k in ks)
                 + f"{s['level_bias']:+8.1f}")
    return '\n'.join(L)


def view_bars(rep, stem='mix', bars=None, page_size=32):
    st = rep['stems'][stem]
    rows = st['rows']
    b0, b1 = bars or [1, min(len(rows), page_size)]
    if b1 - b0 + 1 > page_size:
        b1 = b0 + page_size - 1
    L = [f"[{stem}] bars {b0}-{b1} (A=yours, B=ref)"]
    pitched, drums = st['pitched'], st['drums']
    head = f"{'bar':>4} {'lvlA':>6} {'lvlB':>6} " + ' '.join(f"{n[:4]:>5}" for n in FE.BAND_NAMES)
    if pitched:
        head += f" {'note':>5} {'pc':>5} {'atk':>5} {'#nA':>4} {'#nB':>4}"
    if drums:
        head += ' ' + ' '.join(f"{l[:4]:>5}" for l in LANES)
    head += f" {'shape':>5}"
    L.append(head + "   (band cols = A-B dB; F1 cols 0..1, '-' = nothing on either side)")
    for r in rows[b0 - 1:b1]:
        line = f"{r['bar']:>4} {r['level_a']:6.1f} {r['level_b']:6.1f} " + ' '.join(f"{v:+5.0f}" for v in r['dbands'])
        f = lambda v: '    -' if v is None else f"{v:5.2f}"  # noqa: E731
        if pitched:
            line += f" {f(r['note_f1'])} {f(r['pc_f1'])} {f(r['attack_f1'])} {r['n_notes_a']:>4} {r['n_notes_b']:>4}"
        if drums:
            line += ' ' + ' '.join(f(r['hit_f1'][l]) for l in LANES)
        line += f" {f(r['shape'])}"
        L.append(line)
    if b1 < len(rows):
        L.append(f"(more: bars={[b1 + 1, min(len(rows), b1 + page_size)]})")
    return '\n'.join(L)


def _score_row(r, pitched, drums):
    sc = min(abs(r['dlevel']), 20) / 10 + np.mean(np.abs(r['dbands'])) / 10
    if pitched and r.get('note_f1') is not None:
        sc += (1 - r['note_f1']) * 1.5 + (1 - (r['attack_f1'] or 0)) * 0.5
    if drums:
        sc += sum(1 - v for v in r['hit_f1'].values() if v is not None) * 0.5
    return sc


def view_worst(rep, stem='mix', metric=None, n=10):
    st = rep['stems'][stem]
    rows = [r for r in st['rows'] if r['active_a'] or r['active_b']]
    if metric:
        def key(r):
            if metric in ('dlevel',):
                return -abs(r['dlevel'])
            if metric.startswith('hit_'):
                v = r['hit_f1'].get(metric[4:])
            else:
                v = r.get(metric)
            return 99 if v is None else v
        rows = sorted(rows, key=key)[:n]
    else:
        rows = sorted(rows, key=lambda r: -_score_row(r, st['pitched'], st['drums']))[:n]
    bars = sorted(r['bar'] for r in rows)
    return view_bars_list(rep, stem, bars) + f"\nzoom into one with cmp_zoom(bar=..., stem='{stem}')"


def view_bars_list(rep, stem, bars):
    st = rep['stems'][stem]
    lines = view_bars(rep, stem, [bars[0], bars[0]]).split('\n')[:2]
    lines[0] = f"[{stem}] bars {', '.join(map(str, bars))} (A=yours, B=ref)"
    body = [view_bars(rep, stem, [b, b]).split('\n')[2] for b in bars]
    return '\n'.join(lines + body)


def view_zoom(rep, out_dir, cache_dir, bar, stem='mix', layers=('level', 'hits', 'notes'), bars=1, min_db=-40):
    """Step-by-step A vs B for one (or a few) bars. Note/hit cells: b = sounding in both, r = reference only (you
    miss it), y = yours only (extra), . = neither; UPPERCASE = the note starts on that step."""
    from .analysis import Grid
    FA = dict(np.load(os.path.join(out_dir, f"A_{stem}.npz")))
    gb = Grid(*rep['grid_b'])
    FB = FE.extract(rep['b'][stem], gb, cache_dir=cache_dir)
    spb = FE.steps_per_bar(FB)
    sl = slice((bar - 1) * spb, (bar - 1 + bars) * spb)
    n = sl.stop - sl.start
    L = [f"[{stem}] zoom bars {bar}-{bar + bars - 1}, {spb} steps/bar  (A=yours, B=ref)"]
    beat = spb // int(rep['grid_b'][2])
    rule = ''.join(str((i // beat) % int(rep['grid_b'][2]) + 1) if i % beat == 0 else '.' for i in range(n))
    L.append(f"{'':>12}{rule}")

    def digits(x, top):
        return ''.join('.' if v < top - 36 else str(int(np.clip(round(9 + (v - top) / 4), 0, 9))) for v in x)
    if 'level' in layers:
        top = max(FA['level'][sl].max(), FB['level'][sl].max())
        L.append(f"{'level  B':>12}{digits(FB['level'][sl], top)}")
        L.append(f"{'A':>12}{digits(FA['level'][sl], top)}")
    if 'bands' in layers:
        for i, nm in enumerate(FE.BAND_NAMES):
            top = max(FA['bands'][sl, i].max(), FB['bands'][sl, i].max())
            L.append(f"{nm[:6] + '  B':>12}{digits(FB['bands'][sl, i], top)}")
            L.append(f"{'A':>12}{digits(FA['bands'][sl, i], top)}")
    if 'hits' in layers and st_has(rep, stem, 'drums'):
        HA, HB = FE.hit_sets(FA)[sl], FE.hit_sets(FB)[sl]
        for li, lane in enumerate(LANES):
            row = ''.join('B' if a and b else 'R' if b else 'Y' if a else '.' for a, b in zip(HA[:, li], HB[:, li]))
            L.append(f"{'hit ' + lane:>12}{row}")
    if 'notes' in layers and st_has(rep, stem, 'pitched'):
        SA, SB = FE.note_sets(FA)[sl], FE.note_sets(FB)[sl]
        AA, AB = FE.note_attacks(FA)[sl], FE.note_attacks(FB)[sl]
        used = np.nonzero(SA.any(0) | SB.any(0))[0][::-1]
        for p in used:
            row = []
            for i in range(n):
                a, b = SA[i, p], SB[i, p]
                if not (a or b):
                    row.append('.')
                    continue
                ch = 'b' if a and b else 'r' if b else 'y'
                if (a and AA[i, p]) or (b and AB[i, p]):
                    ch = ch.upper()  # a note starts on this step
                row.append(ch)
            L.append(f"{midi_to_name(p + FE.PITCH_BASE):>12}{''.join(row)}")
        tp = int((SA & SB).sum())
        L.append(f"notes: both {tp}, ref-only (missing) {int((SB & ~SA).sum())}, yours-only (extra) {int((SA & ~SB).sum())}")
    return '\n'.join(L)


def st_has(rep, stem, kind):
    return rep['stems'][stem][kind]


def arrangement_compare(rep, out_dir, cache_dir):
    """Stacked arrangement maps A vs B per stem (same scale per stem pair)."""
    from .analysis import Grid
    gb = Grid(*rep['grid_b'])
    L = ["ARRANGEMENT A (yours) vs B (ref): one char per bar, shared scale per stem, 9 = loudest, -4 dB per step"]
    first = True
    for stem in rep['stems']:
        FA = dict(np.load(os.path.join(out_dir, f"A_{stem}.npz")))
        FB = FE.extract(rep['b'][stem], gb, cache_dir=cache_dir)
        la, lb = ST.bar_levels(FA), ST.bar_levels(FB)
        nb = min(len(la), len(lb))
        top = max(la[:nb].max(), lb[:nb].max())
        if first:
            m, o = ST.ruler(nb)
            L += [f"{'':>12} {m}", f"{'bar':>12} {o}"]
            first = False
        L.append(f"{stem + ' B':>12} {ST.level_row(lb[:nb], top)}")
        L.append(f"{'A':>12} {ST.level_row(la[:nb], top)}")
    return '\n'.join(L)
