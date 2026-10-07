GUIDE = """ismail: a DAW you operate with text. You write notes/patches/effects as data, render, and read audio back as text.

MACHINE
- The computer is shared by several sessions. machine_status before anything over a minute; heavy ops (render,
  separate, mimic_measure, fits, live_parity) take a slot and refuse with the reason when it is busy or hot. Run
  Blender, whisper, demucs or long scripts through `python -m ismail.machine run --gpu|--cpu -- <command>`.

ROLE
- Making music (the default): write only inside songs/<slug>/. Do not edit ismail/, skills/, tests/ or another
  song, and do not run git in the ismail repo. Missing a capability: build it in the song (voices/, work/) and list
  it in songs/<slug>/HANDOFF.md. Changing ismail itself only when the user asks: skills/ismail/references/development.md.

MEASURE FIRST
- Songs got real where their sounds and numbers were measured from an example, and stayed fake where they were
  guessed. Before writing a patch for an acoustic or electric part, get an example and measure it: mimic_measure,
  sound_extract or analyze_kit + instrument_fit, track_fit, a sampler of an imported recording. Loose files work
  as sources (a path). project_info shows each track as measured / designed / unstated; track_model records an
  example or marks a sound designed on purpose. Write a measuring script only for what no op measures, and list
  it in HANDOFF.md.
- Every recording, video or score in the song's ref/ gets a row in a SOURCES file before it is used (title, link,
  who made or played it, licence, what was measured): project_info and render name the files that have none, and
  credits writes CREDITS.md from the rows when the piece goes public.

THE PERSON
- Their words are data: lexicon_note(said=<verbatim>, craft=...) when they name a quality or a problem, map it
  (means=...) once you know, set the outcome later; lexicon_find before acting on a word or explaining a change;
  lexicon_view at the start of a session. Words about the work only, never emotions. Every piece states its
  objective in their words (project_set(objective=)); versions use project_new(derived_from=). Read
  references/user-experience.md.

CONVENTIONS
- Every tool takes `project` (a directory). Bars are 1-indexed; ranges [a, b] are inclusive.
- Note text: '<beat> <pitch> <dur_beats> [vel] [@-40ms]' per line or ';'-separated, beats relative to the target bar
  (0 = beat 1). Chords 'C4,E4,G4'. C4 = 60. ' #' starts a comment.
- Sounds land by their start. A sound whose attack comes late (a measured phrase, a bowed note, a bird call)
  is nudged: '@-40ms' on a note, or track_set(offset_ms=-35) for a whole part; the note stays on its beat.
  Never time-warp a recording to land it.
- Audio sources for analysis: render | ref | ref:drums|bass|other|vocals | track:<name> | sound:<name> | a path.
  Omitted source = the reference if the project has one, else your render. track:<name> needs render(stems=True)
  and is the track after its fx and fader, scaled by the master chain's gain (tracks sum to the mix).
- New projects get a master limiter (ceiling -0.3 dB); render reports its gain reduction. Keep it under ~6 dB.
- Volume automation is an OFFSET (dB) on the track fader. Hz params automate in log space.
- Every mutation snapshots the project; `undo` walks back. `batch` applies many ops atomically.

BUILD
  project_new -> track_add(instrument = dict | 'preset:x' | 'track:y') -> notes_write / pattern_write
  -> fx_add / bus_add / track_set(sends, output) -> automation_set -> render(bars=[a,b] for fast loops)
  instrument_help(type) and fx_help list every parameter with defaults.
  Voices: engineered instruments kept as Python modules (grand_piano, growl, sfx ...): voices_list, voice_help;
  use {"type":"code","voice":"<name>"} or 'preset:<name>'. Write a song's own in <project>/voices/<name>.py
  (defines voice(freq, t, vel, gate, sr)); it overrides a built-in of the same name.
  Sounds: sound_make (any instrument+fx -> bank), sound_speak (TTS), sound_import, sound_extract (average a
  repeated event), audio_place; use bank sounds in a sampler, as wavetables, or as vocoder modulators.

HEAR (audio -> text), cheapest first
  analyze_structure   whole-song arrangement map (bands, stems, root per bar), sections, loop length
  analyze_bars        per-bar level/bands/onsets/chroma      analyze_envelope  level per 16th (pumping, gates)
  analyze_drums       drum lanes as step strings             analyze_pitches   notes sounding per beat
  analyze_roll        piano roll of a source (same features the comparisons score)
  analyze_melody      monophonic line -> notes               analyze_formants  vowels of a voice
  analyze_timbre / analyze_spectrum / sound_compare          spectrogram (PNG, last resort)
  analyze_kit         drum kit pieces (NMF) + their patterns analyze_swing     how late swung hats land
  analyze_sections    loudness per section, dynamic range, a build as loud as its climax

LIVE (play in real time while you edit; a separate engine process per project folder)
  live_start(bpm) -> live_track(track, instrument) -> live_queue([{track, notes | lanes, bars, loop, at}, ...])
  -> live_status / live_view / live_listen(bars) -> more live_queue ... -> live_stop.
  Clips loop until replaced, so the music keeps going between your turns: work at phrase scale (next_4, next_8),
  put per-beat variation inside the clip, and chain clips with at='after:<id>' to pre-program an arc.
  A clip replaces what its track would play from its start; {track, stop: true} silences one. Launch bars move
  later when the first notes cannot render in time (the reply says so). Keep the runway (live_status) longer than
  any slow job you start (sound design, fitting). live_listen is the same analysis as HEAR, on the last bars
  played. The output always passes a trim, a loudness cap and a limiter; watch their gain reduction in
  live_status and balance with volume_db instead of pushing.
  Effects: live_track(fx=[...]) sets a track's chain (fx_help; 'track:<name>' copies a project track's chain),
  live_bus + sends={bus: dB} share one hall/delay across tracks, live_fx(target, index, params, ramp_beats)
  moves automatable params (a sweep, a fade, a build) without resending the chain. Replaced chains ring out.
  Every built-in effect, the guitar rig included, runs live as in the studio; one with no live version would be
  baked into each rendered note (live_status marks it; no live_fx on it).
  A performer voice (module with perform(), e.g. electric, kit70) renders a bar at a time with the part before it
  as context; clip expr={'bend': [[beat, semitones], ...]} drives its lanes, as automation inst.lane.<name> does in
  the studio.
  live_parity(song, bars) checks that a song section plays on a deck as it renders.
  Decks: live_load(deck, song=<ismail project folder>, bars=[a, b]) puts a whole song on a deck (cued, off air,
  while another deck plays); live_listen(deck=...) hears the cued deck; live_transition(to, style, bars) queues
  the mix (blend | bass_swap | filter | cut); live_deck sets fader, 3-band isolator (kill at -40), filter knob,
  transpose. live_status shows the mixer load: keep it under ~70%.

RECREATE A REFERENCE (what worked)
  1. analyze_grid -> project_set(bpm, offset_sec); then align(a='track:<drum>', b='ref:drums') and correct
     offset_sec by the reported lag. Timing errors poison every other metric. It also prints the tuning: a record
     15+ cents off A440 needs ref_retune() before any pitch reading, or every note reads as two semitones.
  2. separate(source='ref') (or project_set reference_stems=...) and analyze_structure(source='ref').
     analyze_swing and analyze_kit(source='ref:drums') before writing any drums: measured, never guessed.
  3. Transcribe with notes_from_audio_loop (consensus over loop repetitions). Raw notes_from_audio copies echoes,
     leakage and distortion partials as hard notes: it scores well and sounds like clutter.
     Route registers of one stem to different tracks with low/high.
  4. Sound design: sound_extract a repeated hit/stab (e.g. every=8 for one position of an 8-bar loop), then
     instrument_fit(target='sound:x', params={path: [lo, hi]}, fx=[...]) - fx params fit too ('fx.0.depth_db').
  5. stem_map_set({track: stem}) -> render(stems=True) -> levels_from_ref (faders from the reference's stem
     balance, not by feel) -> cmp_run. Read cmp_summary, then drill:
     cmp_arrangement (macro), cmp_sections, cmp_worst / cmp_bars (per bar), cmp_zoom(bar) (per 16th:
     b both, r reference only, y yours only, UPPERCASE = note start). cmp_list = progress over time.
  6. For fair stem scores use cmp_run(stems='demucs') at checkpoints: your render is separated by the same
     model, so leakage is symmetric.

READING SCORES
  closeness per metric: 0 = the reference against itself half a loop off ('plausible but wrong'),
  1 = the reference against itself one loop later (its own natural variation). Groups: notes, rhythm, clean
  (clutter, loop self-consistency, extra attacks), sound (bands, level, transients, level shape),
  perceptual (CLAP embedding similarity). Handcrafted metrics can all look good while it still sounds
  different: trust the perceptual group and the warnings, and never call a match done on notes alone.
"""

FIRST_SESSION = """FIRST SESSION: this person has made nothing with ismail yet (no finished render, no {marker}).
Their first try decides whether they come back. Run it this way, then the normal loop:
1. Two sentences on what this is: you write the music as notes and instruments, render it and read it back as
   numbers, and they judge it by ear; everything stays as editable files on their machine.
2. At most two questions before any sound: what it is for, and do they play (an instrument, a style they
   trained in, reading music); then a mood or a reference if they have one (skip it when the first answer gave
   one). Ask nothing before this, and no
   recording: one is welcome, never required; the sketch they pick is the song's example (loop step 0).
   Their first answer decides your words:
   guide(first_answer=<their words, verbatim>) says musician or plain words, and you keep to it from then on.
3. Sound within about five minutes: sketch(project, brief=<their words>) reads their tempo, key, genre,
   instruments and form, writes three readings on the measured voices below and renders them. While it works, say
   what is happening and about how long (the first sketch within about two minutes, often sooner; silence reads as
   broken).
   A busy machine answers BUSY: say "the computer is busy, I'll try again in a moment" and call it again.
   It returns as soon as the first is ready: read its SAY TO THE PERSON block out as it is (what each sketch is,
   what was swapped, what it can't make yet) and play version 1 at once; sketch_wait(project) says when the others
   land.
   Open each for them, one at a time, and ask which is closest or what each is missing; their correction is the
   next round:
   sketch(project, <their words>, base='<version>'): it keeps their tune and chords, changes only what the words
   name, and its reply says which version is before and which after: play both. Version numbers go on across
   rounds, so a number always names the same sketch. An instrument they named that has no voice is a later step:
   offer to find an example of it and build it (the loop's step 0), never pretend the stand-in is it.
4. sketch_keep(project, '<version>') makes the pick the song and ends the first session. A change after that
   is the same route: sketch(..., base='<the kept version>'), then sketch_keep(..., replace=True) if they prefer it.
5. Short rounds: one named change at a time, two versions played in turn, "which one?".
6. Early on, one deliberate small edit: "change just one thing" (a warmer bass from bar 5, drums out for two
   bars); change only that, quickly, and play before and after. A generator cannot do this.
7. At the end: where their files are, what it took (minutes, renders), and one line on the depth: recreate a
   reference, build an instrument from recordings, play live, the VR stage.
8. The rest waits for its moment, one feature at a time, one sentence, offered and never explained up front (not in
   the opening, not in the first sketch): when they keep a sketch or say they like one, that they can hear it on
   their phone and talk back while it plays (phone_start; reaching it away from home needs Tailscale, which you set
   up if they want it); when they want to jam, perform or hear it change while it plays, live play; when they
   mention a VR headset or want to see the music, the stage; when they step away while it plays, the phone again.
   If they say no, don't offer that one again this session.
Showcase voices (measured; voices_list marks them *):
{showcase}"""


# ------------------------------------------------------------------ the first answer decides the vocabulary

import re as _re

_INSTRUMENTS = ('piano', 'keyboards?', 'keys', 'organ', 'harpsichord', 'synths?', 'guitars?', 'bass', 'drums?',
                'percussion', 'violin', 'viola', 'cello', 'double bass', 'contrabass', 'flute', 'clarinet', 'oboe',
                'bassoon', 'sax(?:ophone)?', 'trumpet', 'trombone', 'french horn', 'horn', 'tuba', 'harp', 'ukulele',
                'banjo', 'mandolin', 'accordion', 'harmonica', 'fiddle', 'oud', 'sitar', 'tabla', 'turntables',
                'bagpipes', 'recorder', 'marimba', 'xylophone', 'vibraphone', 'timpani', 'tin whistle', 'bouzouki',
                'kora', 'erhu', 'koto', 'guzheng', 'darbuka', 'djembe', 'cajon', 'guembri', 'ney', 'qanun',
                'by ear', 'an instrument')
_PLAYERS = ('pianist', 'organist', 'keyboardist', 'guitarist', 'bassist', 'drummer', 'percussionist', 'violinist',
            'violist', 'cellist', 'fiddler', 'flautist', 'flutist', 'clarinettist', 'clarinetist', 'oboist',
            'saxophonist', 'trumpeter', 'trombonist', 'harpist', 'singer', 'vocalist', 'chorister', 'cantor',
            'conductor', 'composer', 'arranger', 'songwriter', r'(?<!podcast )(?<!video )(?<!film )producer',
            'beatmaker', 'dj', 'musician', 'choir director', 'choirmaster', 'music teacher')
_SIGNALS = [                                     # plays an instrument, sings, reads music, trained
    r"\b(?:play|plays|played|playing)\s+(?:a\s+little\s+|some\s+|the\s+)?(?:" + '|'.join(_INSTRUMENTS) + r")\b",
    r"\b(?:" + '|'.join(_PLAYERS) + r")s?\b",
    r"\bi\s+(?:also\s+)?sing\b|\b(?:sing|sang|sung)\s+in\b|\b(?:in|with)\s+(?:a|my|our)\s+band\b",
    r"\bread(?:s|ing)?\s+(?:sheet\s+)?(?:music|notation|scores?|charts|lead sheets)\b|\bsight[- ]?read",
    r"\b(?:classically\s+)?trained\b|\bstudied\s+(?:music|piano|organ|guitar|voice|jazz|classical|composition|"
    r"theory|harmony)\b|\bconservatory\b|\bmusic\s+(?:school|degree)\b|\bgrade\s+\d\b|\b(?:[a-z]+\s+)?lessons\b",
]
_NEGATORS = _re.compile(r"\b(?:don'?t|do not|doesn'?t|didn'?t|can'?t|cannot|never|not|no|nor|neither)\b")
_CLAUSE = _re.compile(r"[.;!?\n]|,\s*but\b|\bbut\b|\bthough\b|\balthough\b")
# a few trades whose own words an agent should use back to them
_TRADE_WORDS = [
    (('organ', 'organist', 'cantor', 'choir', 'chorister', 'choirmaster'),
     'stops and registrations, manuals and pedals, verses and cadences by name'),
    (('piano', 'pianist', 'keyboard', 'keys', 'harpsichord'), 'the left hand and the right hand, voicings, pedalling'),
    (('drum', 'drummer', 'percussion', 'percussionist'), 'the groove, the pocket, fills, kick, snare and hats'),
    (('sing', 'singer', 'vocalist', 'sang', 'sung'), 'the line, phrasing, breath, the parts (soprano to bass)'),
    (('violin', 'viola', 'cello', 'fiddle', 'violinist', 'cellist', 'fiddler', 'contrabass', 'double bass'),
     'bowing, legato and detached, positions, the section'),
    (('guitar', 'guitarist', 'bass', 'bassist', 'banjo', 'ukulele', 'mandolin'),
     'chord shapes, strumming and picking, the low and high strings'),
    (('producer', 'beatmaker', 'dj', 'turntables', 'synth'), 'bars, the drop, the mix, sends and sidechain'),
]


def vocabulary(first_answer: str):
    """Read a person's first answer: are they a musician (they name an instrument they play, a style they trained
    in, or reading music)? Returns (kind, their words that decided it, trade words to use or None), kind
    'musician' or 'plain'. A negated mention ("I don't read music") does not count."""
    text = ' '.join((first_answer or '').split())
    low = text.lower()
    found = []
    pos = 0
    for clause in _CLAUSE.split(low):
        start = low.find(clause, pos)
        pos = start + len(clause)
        for pat in _SIGNALS:
            for m in _re.finditer(pat, clause):
                before = clause[max(0, m.start() - 30):m.start()]
                if _NEGATORS.search(before.split(',')[-1]) or _NEGATORS.search(m.group(0)):
                    continue
                found.append(text[start + m.start():start + m.end()])
    if not found:
        return 'plain', [], None
    words = ' '.join(found).lower()
    trade = next((t for keys, t in _TRADE_WORDS if any(_re.search(r'\b' + k, words) for k in keys)), None)
    return 'musician', list(dict.fromkeys(found)), trade


def vocabulary_text(first_answer: str) -> str:
    kind, said, trade = vocabulary(first_answer)
    if kind == 'musician':
        heard = ', '.join(f'"{s}"' for s in said)
        return (f"VOCABULARY: musician (they said {heard}). Use their words from now on, the way they would say it"
                f"{': ' + trade if trade else ''}; bars, keys and chord names are fine. Ask what they would call a"
                " thing before you name it, note each trade word with lexicon_note(said=<verbatim>, craft=<their"
                " craft>), and keep to it for the whole session. Do not explain what they already know.")
    return ("VOCABULARY: plain words (nothing in their answer says they play, trained or read music). Say what a"
            " thing does, not its trade name: \"the low notes\", \"the part that comes back\", \"softer\"; times in"
            " minutes and seconds, not bars; no keys, chord numbers or Hz unless they use them first. When they"
            " name a quality in their own words, note it with lexicon_note and use their word back. If they later"
            " say they play or read music, call guide(first_answer=<those words>) again.")
