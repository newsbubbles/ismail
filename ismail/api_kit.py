"""The kit op (ledger M295): every public voice and effect by family, as text for agents. The data and the page
(docs/KIT.md, docs/kit.json) come from ismail/kit.py; this op reads the sources live, so it is never stale.
Registered into api.OPS on import."""
from . import kit as K
from .api import op, OpError


@op()
def kit(project: str = None, family: str = None, voice: str = None) -> str:
    """What instruments and effects ismail has, by family: each public voice with its type (sampled, measured,
    mimic, sprite, physical, designed), what it is modeled on (an instrument type), its ear state (passed, usable,
    failed, pending, untested) with the exam or ledger row that judged it, its range and its render cost, and the
    gaps still open in each family. Reach for it when someone asks what instruments exist, whether there is an organ,
    a sax or an 808-style kit, which voices passed a blind listening test, or what an instrument family still lacks.
    family: one family (keys, organs, guitars, basses, bowed strings or strings, woodwinds or winds, brass, mallets,
    voices, drum kits or kits, hand percussion, analog synths, drum machines, FM, wavetable, granular, fx), 'synths'
    for every synth family, an effect family (level and tone, dynamics, drive, rig, time, modulation, voice, space)
    or 'effects' for all of them; with a family its open gaps are listed too. voice: one voice or effect in full
    (how to use it, what it needs to download, its lanes). The same data as a page: docs/KIT.md."""
    data = K.build()
    out = K.text(family=family, voice=voice, data=data)
    if out is None:
        if voice:
            raise OpError(f"no public voice or effect {voice!r}; kit() lists them all, voices_list and "
                          f"presets_list list what a song can play")
        fams = data['families']['voices'] + data['families']['effects']
        raise OpError(f"no family {family!r}; families: {', '.join(fams)}, or 'synths', 'effects'")
    return out
