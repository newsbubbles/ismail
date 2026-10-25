# Changelog

## Unreleased

### voices: trumpet, a measured lip-reed performer

- New built-in voice `trumpet` (`ismail/voices/brass/trumpet.py`), a performer: `{"type": "code", "voice":
  "trumpet", "tail": 1.5}`. A Bb trumpet in concert pitch, E3-D6 as measured (to F6 thinner): an additive lip reed
  whose tone per pitch is fitted to the University of Iowa MIS Bb trumpet, under one brass law for dynamics,
  attacks, releases and swells (softer blowing is darker by harmonic number). An overlap slurs, a gap tongues.
  Velocity is blowing (127 ff, 90 f, 55 mf, 35 mp, 22 p). Mutes `open`, `harmon`, `harmon_stem` (param `mute`);
  lanes `bend`, `vib`, `level`, `breath`, `tongue`, `scoop`, `fall`, `doit`, `shake`, `flutter`, `growl`,
  `halfvalve`, `plunger`, `jitter`, `top`. (ledger:M331)
- Built and judged in a winds song study: blind picture rounds at or under chance (the lip shake still caught),
  five single notes in a blind ear round (four not told apart, the plunger reversed), and a blind phrase round on a
  phone speaker passed ("I cannot tell the difference at all"). Its numbers are the study's: the defaults render
  sample for sample as the study's voice did. The phrases that passed copied a player's take each; INFO's `tips`
  gives those settings.
- Scoop, fall, doit and half-valve curves and the room floor are measured from Jon Harnum's trumpet effects videos
  (CC BY, credit Harnum): credited in its INFO and in docs/reference.md, Sample sources. Its INFO lists which
  constant came from which source.

### mimic perform: each slur's move inside the margin Nate named

- Each slur in `"perform": true` now draws its own cross (ledger:M327). Nate on the #358 confirm round (clarinet,
  phone, 2026-10-25): on a stepping tune both the old glide ("slide") and the fingered slur ("fingers") pass, "maybe
  somewhere between those two would be like the perfect fit but I think those two represent like the margins of where
  we should be inside of"; on leaps he picked the fingers and called the glide "slide, too slow". New param
  `slur_vary` (0..1, default 1): a step (2 semitones or less) draws its cross uniformly in
  [`slur_cross` x (1 - `slur_vary`), `slur_cross`], so with the defaults anywhere from the glide to the fingers; a
  leap (more than 2 semitones) draws in the top quarter of that margin, near the fingers. The pitch move and the
  colour cross-fade read the same draw, keyed on the slur's song time, so a window renders as the whole does.
- The 10-90 % pitch move per slur, read with yin as the round measured it (clarinet, 80 slurs each, p10 / p50 / p90):
  steps 14.5 / 20.3 / 29.0 ms (sd 5.2; real flute slurs: sd about 6 around 13), leaps 5.8 / 8.7 / 10.2 ms, against
  13.1 / 14.5 / 16.0 for #358 alone and 26.0 / 31.9 / 37.7 for the glide. Every performed slur is a new take;
  `"slur_vary": 0` renders exactly what #358 did and `"slur_cross": 0` exactly the glide of before (tests compare
  against saved copies of both).

### phone: a long path in the DJ log wraps

- A DJ log note with a full file path made the phone page wider than the screen and pushed every other control
  sideways (Nate, 10-24). The log rows' text column is `minmax(0,1fr)` now and wraps anywhere, so a path breaks inside
  its row.
- The same for a panel's heading and text: a path there scrolled the panel sideways (1156 px of content in a 375 px
  panel); it wraps now.

### voices: flute, a measured air-jet performer

- New built-in voice `flute` (`ismail/voices/winds/flute.py`), a performer: `{"type": "code", "voice": "flute",
  "tail": 1.5}`. An additive air jet whose tone tables (H1-H12 and the air per band, per note and dynamic, B3-Db7)
  are fitted to the University of Iowa MIS flute; slurs, legato tonguing and flutter from the Philharmonia flute
  phrases; vibrato growth and breath attacks from public-domain and CC solo recordings (credits in its INFO and in
  docs/reference.md, Sample sources). An overlap slurs as a cross-fade of harmonics (no glide), a gap under 0.1 s
  tongues under the same breath, a gap of `breath_gap` or more breathes. Velocity is blowing. Lanes `bend`, `vib`,
  `level`, `breath`, `tongue`, `scoop`, `fall`, `flutter`. Built and judged in a song study (blind picture rounds,
  a blind ear A/B in a mix, a sighted pass in a mix; no blind round against a player yet). (ledger:M321)
- Its defaults are the study's measured ones plus the fingered settings passed by ear: `slur_cross` 1,
  `slur_detail` 0, `keys` 0.45, `breath` 0.7, and `entry_vel` 8 (a phrase's first note shorter than a beat is blown
  a little firmer). INFO's `tips` gives the settings for a line slurred almost every note with a late vibrato.
- A phrase's first note's attack is capped at a share of its length at every length (the study capped only notes
  under 0.2 s), so a 0.26 s pickup is no longer swallowed: on 8 pickups slurred into held notes, the pickup 40-120 ms
  in sat at most 4.8 dB under the held note (was 10.3), median 3.0 (was 5.3).
- Four study params nobody set were left out: `floor_hi`, `chiff`, `part_spread`, `lane_tilt`.

### mimic perform: fingered slurs by default, and when the vibrato comes in is a lane

- A slur in `"perform": true` is now a fingered slur (ledger:M322). Nate on the clarinet performer's blind round,
  2026-10-24: the 45 ms glide "doesn't sound like a finger covering a hole, it sounds like a MIDI instrument sliding
  up", while he liked how the note's profile changes at the join. New param `slur_cross` (0..1, default 1): at 1 the
  pitch moves to the new note in about 4.5 ms and each partial's level hands over from the old note's to the new
  note's over `slur_color_ms` (20 ms, new), each partial from its own start (up to about 15 ms in). The join, 10-90 %
  of the pitch move, read from the render (D4 to F4, 23 slurs): 5.6 / 5.9 ms p50 / p90, against 26.6 / 33.9 ms with
  the glide. The level dip at the join is now about -3.6 dB in one 10 ms frame (the glide's was -2.8 dB over 40 ms);
  the -0.8 dB slur dip is kept, at least 12 ms wide. No second attack: the new note's harmonics are at +0.3 dB re
  held in the 60 ms after the join. This is a new take for every performed slur; `"slur_cross": 0` renders exactly
  what it did before (a test compares against a saved copy of the old performer).
- Lanes `vib_delay` and `vib_grow` (s, read at each note's start, ledger:M323): when the vibrato comes in and how
  fast it grows, per note ("coming in slightly late ... the agent should be able to control"). Left out, the params of
  the same names, as before; each note still varies them by about 20 %.

### mimic: short notes no longer drift tens of cents off

- mimic's slow random curves (pitch drift, vibrato rate and depth wander, swell, shimmer) were scaled by the std of
  the note's own slice. A short slice of a slow curve is nearly flat, so it was stretched to full depth: a 0.1 s
  cs80_lead A4 note sat 27 cents off at the median and 81 at the 95th percentile, against 13 and 37 now. The curves
  are now scaled by the smoothing window's expected std, so a short note gets a short stretch of the same curve a
  long note has. Notes of 10 s and more keep their depth within a few percent. Every mimic note changes (the random
  draws differ), so this wants an ear round. (ledger:M306)

### master_cue ends at its true-peak ceiling, and its mp3 under it

- `master_cue` gets a lookahead true-peak catch as its last stage (ledger:M280; M271 h and M277 a ask the same):
  the 4x oversampled peak of each sample, the gain it needs min-filtered over +-1.5 ms, a 25 ms release and a
  1.5 ms moving average, no re-gain after it. Ported from `songs/planet_rock_axel_f_remix/work/master.py`. The
  limiter's rounds before it now get up to 6 rounds, each aiming lower by what the last re-gain put back over (at
  most 1.5 dB under the ceiling), so the catch only takes what a target too loud for the mix leaves; the report
  says what it took and where the loudness lands. The wav ends at the ceiling, `tp`.
- `mp3=True` also writes `<out>.mp3` (320k, ffmpeg lame) from a copy caught again at `tp - mp3_catch_db`
  (`mp3_catch_db=1.2`), because a 320k encode lands up to 1.25 dB over its wav. Only the mp3 gets the extra catch;
  the wav stays as mastered, at its full loudness. The report gives the copy's catch and the decoded mp3's true peak,
  with a warning if it is still over `tp`. The margin is from a sweep of 90 masters per margin with ismail's encoder
  (5 synthetic dense mixes, 3 seeds, 44.1 and 48 kHz, 0 to 4 dB of limiting asked): the mp3 peaked 0.1 to 1.25 dB
  over its wav (median about 0.5); a margin of 1.0 left 1 mp3 in 90 over -1 dBTP in one run and peaked at -1.03 in
  another, and 1.2 left none (the worst at -1.18). `mp3_margin_db` (0 by default) still lowers the wav's own ceiling.
- On the test mix (4 s at -18.6 LUFS, 3 dB of limiting asked at -1 dBTP): before, the wav peaked at -0.71 dBTP and
  its 320k mp3 at 0.00; now the wav is at -1.08 at the same loudness (-7.7 LUFS), and the mp3 from the same call at
  -1.61 (its copy caught 1.17 dB). A mix already under the ceiling keeps its loudness.
- This changes existing masters only where the old rounds left the wav over the ceiling: it now ends under it, at
  the same loudness or a little under when the target is too loud for the mix. `tp_catch=False` is the master from
  before, sample for sample. Only `master_cue(mp3=True)` gets the mp3 catch; `render`'s mp3 previews
  (`render.write_mp3`) are unchanged. Tests: `tests/test_master_cue.py` (the mp3 test skips without ffmpeg; CI has
  it on Linux).

### Section reads find the path and name the loudest part

- `analyze_sections` looks up a relative path in the working directory first, then in the project. On the
  planet_rock remix, `songs/<slug>/proj/renders/mix_v7.wav` run from the repo root read "not found". A path in
  neither place now gives an error that names both places it looked. (ledger:M281 a)
- `analyze_sections` with stems adds a last block, one line per section, that names the loudest one or two parts
  and their rms against the mix's. For a render with `stems=True` the parts are the tracks routed to the master
  and each bus (a track on a bus counts inside the bus); for the reference they are its stems. On the remix a vox
  routed to master carried a build that the mix bus had pulled down 9.5 dB, and the section read did not say so.
  Stems from another render are not read, and one line says so. The existing lines are unchanged. (ledger:M281 b)

### instrument_fit takes wait; a band far off costs what it should

- `instrument_fit` refused BUSY with a message to pass `wait='10m'`, then rejected the keyword. It now takes `wait`
  as `render` does ('10m' default, '0' refuses at once), and so does `track_fit`. The parsing is one helper
  (`api.wait_seconds`) shared by `render`, `track_freeze` and every heavy op. A heavy op without `wait`
  (`mimic_measure`, `live_parity`) is no longer told to pass one: its BUSY reply names the shell work-around
  `python -m ismail.machine run --cpu --wait 10m -- python -m ismail -p <project> <op> ...`. (ledger:M313)
- The fit distance took the mean over 29 bands, so two sub bands 35 to 39 dB over the target cost about 2.5 and a
  bass fit kept an octave-down square the target does not have. In `instrument_fit` each dB a band is past 20 dB
  off now adds 0.15, summed, and the band the tables print as 25 Hz (24.8 Hz) counts for fmin=25 (it fell out of
  the distance). A synthetic A1 bass with nothing under 40 Hz: a darker clean bass read 5.37 before and 5.13 now;
  the same tone plus an octave-down square (+36 and +25 dB at 25 and 31 Hz) read 4.45 before (the better score)
  and 9.46 now. The `instrument_fit` reply warns of any band more than 20 dB off. (ledger:M315)
- `sound_compare` scores are unchanged on purpose: song sessions track its number across rounds, and a before and
  after must stay comparable. Its reply now prints the same far-band warning, as text only. To score the way
  `instrument_fit` does, pass `sound_compare(..., fit_score=True)` (in Python, `sounddesign.distance(...,
  **sounddesign.FIT_SCORE)`, that is `far_cost=0.15, nominal_bands=True`). `distance` defaults to the old score,
  so `track_fit` and `mimic_measure`'s leave-one-out check score as before too.

### melody_consensus: a lead line over a backing in the same stem

- New op `melody_consensus(source, reps, phrase_bars, steps_per_beat=4, low='C3', high='C7', track=None,
  write_at=None)` (ledger:M314). On celestial_resort_1024's vocals stem, which also holds a backing part,
  `analyze_melody` warned of a second part within 6 dB on half the notes and `notes_from_audio_loop` doubled the
  lead at the octave. The op ports the song's prototype: every candidate pitch scores its first five harmonics
  on a constant-Q spectrum, the scores are averaged over the bars where the phrase repeats (`reps`), and each
  step takes the best pitch; a new note starts where the pitch changes or the start of a step is louder than
  the step before. Before scoring, what sounds through nearly all of the phrase comes off (`held_pct=10`, each
  bin's 10th percentile over the phrase), so a held chord stops winning. It returns notes_write rows (beats from
  the phrase's first bar) and a short summary: notes per bar, how many repetitions pick each pitch on their own,
  the margin over the runner-up, and the notes whose octave call was within 1.5 dB. `track=` writes the phrase
  at every bar of `write_at` (default: `reps`). On the song's phrase A (bars 17, 25, 49, 57, G4 to C6) it gives
  40 of the prototype's 41 notes, and names the one it reads an octave off as its only close call.

### live_parity: a one-sample offset is the same take, and each track says its render speed

- At 136 BPM a beat is 19455.88 samples, and an `@ms` nudge is never a whole number of samples. Two roundings put
  notes one sample apart (ledger:M285(a)):
  - The studio window's buffer started a pre-roll before the window, a fraction of a sample long. Some notes then
    landed one sample off where the whole song's render has them. The buffer now starts on a whole song sample,
    and a window at any tempo matches the whole song's render sample for sample (it did at 120 BPM only).
  - A deck rounds each note to the engine's own samples, and the engine's bar line sits at a different fraction
    of a sample than the song's. In a set this is inaudible. live_parity now starts its engine's clock so the
    deck's first bar line has the song window's fraction, so its takes are sample for sample.
- live_parity judges each part at its best lag. A take that lines up within 3 ms reads `ok (same, offset 1 sample
  late)`; before, it read `DIFFERS: +1 samples late` at corr 1.000 and level within 0.01 dB. The fit is looked for
  up to 100 ms either way, so a take 50 ms off still DIFFERS and says how far off it is. Before, it was looked
  for up to 256 samples, and a take 50 ms late read "200 samples early".
- Each track's line says how fast it renders live (`renders 41.3x realtime`), from the seconds its renders took in
  the check (ledger:M285(e)).
- Every op is an attribute of `ismail.api`, including those registered outside api.py: `api.live_parity`,
  `api.sound_speak`, `api.master_cue`, `api.analyze_sections` and the rest (ledger:M277(e), M285(e)).

### mimic perform: vibrato by default, and phrase ends that fall

- The default vibrato depth of `"perform": true` is now the median of the profile's notes that have vibrato,
  times (0.5 + 0.5 x the share of notes that have it). The median over all notes gave 0 c on the clarinet (vibrato
  on 4 of its 11 notes), so a performed line was a straight tone and the vibrato bloom never showed. Default before
  and after: clarinet 0.0 to 3.7 c, bassoon 5.0 to 4.5 c, horn 5.0 to 4.9 c, cello 9.6 to 9.3 c, violin 12.7 to
  14.1 c, contrabass 5.0 to 5.0 c. `"perform": {"vib": N}` still sets it.
- A phrase end now falls by `taper_db` with the room on. A clarinet D5 phrase end fell 0.4 dB against the 5 dB
  setting: the tone was steady (no vibrato), so dry and room summed partly out of phase, and the end's pitch sag
  moved it across the room's response so the sum came back up just as the level fell (3.4 dB with the room off,
  3.3 dB with the room on and no sag). The performer now runs the room per line, reads each phrase end after the
  room and takes off what the taper is short of (it only lowers). A phrase ending on a short note (under 0.5 s)
  had no taper at all; the taper now spans the last 35 % of the last note or the phrase's last 0.3 s, whichever is
  longer, and holds its full depth over the last 15 % of that. On four phrase ends with the room on (one note, the
  ear round's D5, a short last note, tongued notes into a long one) the end falls 5.9 to 8.6 dB, against 2.8 to
  6.8 dB before. Found by the Sound Engineer's #346 ear round build.

### Reading a drifting, thumb-led record

- `analyze_grid` reads the thumb/bass band (55-190 Hz) and the finger band (1.5-6 kHz) periods apart and says when
  they disagree. On a bossa guitar the whole-mix tempo locked onto the 3-3-2 finger figure (122 BPM for a thumb at
  92); now the extra lines name the thumb/bass band as the likely beat and give the `bpm_hint` to rerun with. The
  existing lines are unchanged; the new ones come before the swing line. On the Bim Bom guitar stem: thumb band
  92.5 BPM, tempo read 121.92, "rerun with bpm_hint=92.5". (ledger:M287 a)
- `analyze_swing(follow=True)` follows a beat line on a record that drifts instead of one fixed grid, reads every
  16th step on it in `band_hz` (default 1.5-6 kHz) and prints the hits, accents and median offset per step and the
  most-played 16-step strings with the timing inside them; `threshold` sets what counts as a pluck. Ported from
  the bossa reference's prototype. On a click track drifting from 90 to 94 BPM, planted +20 and -15 ms offsets read
  +21 and -13 ms. Without `follow`, one line says when the beat drifts more than 3%. (ledger:M287 b)

### Notes that clash, read from the arrangement

- `notes_clash` (ledger:M282): notes from different pitched parts that sound together a semitone apart (a minor
  2nd, a minor 9th, or those plus octaves; a close major 7th is a chord colour and not listed) for at least
  `min_overlap` beats (0.1 by default, so passing tones drop out), and notes out of the key. The key is the one
  given, the project's or the sketch's, else found from the notes; a minor key allows its raised 7th. Each finding
  gives the bar and beat, the two parts and their notes, the overlap and the velocities; a place in the bar that
  repeats is one finding with its bars. Sorted by how audible (the quieter note's level times the square root of
  the overlap), top `n` per list, counts for all. Drums, fixed-pitch samples and synths, drum and effect voices,
  unmapped pads and muted tracks are skipped and named; a kit's pitched pads are read. Ported from the
  planet_rock_studio prototype: on that song it lists the same 37 clashes in the deck and the 7 kept in the studio
  version, and no note out of G minor.

### mimic plays phrases: perform=true for legato winds and strings

- `{"type": "mimic", "profile": ..., "perform": true}` renders a track as phrases instead of note by note, for any
  sustained (blown or bowed) profile. Overlapping notes slur (one continuous tone, the pitch glides, no second
  attack), a gap under `breath_gap` (0.16 s) is tongued, a longer gap is a new breath. The vibrato waits and grows
  (the profile's measured vibrato by default) with a breath pulse, and a long last note tapers. Lanes, as
  automation `inst.lane.<name>` or clip `expr` live: `level`, `vib`, `scoop`, `tongue`, `breath` and `bend`, the
  tenor sax's names where they overlap. `"perform": {...}` sets performer params (`instrument_help(type='mimic')`
  lists them). A decaying profile (piano, harp) refuses it with a pointing error; left out, every render is as it
  was. On a clarinet slur the second note's harmonics in the first 70 ms after the join sit 9.9 dB under their
  held level note by note (a new attack) and 2.3 dB under performed. (ledger:M308, from the
  bossa_gilberto_studio flute performer, after Nate's "the flute sounds a little bit midi" of 10-23.)

### Velocity sets the level of rhodes and emily

- `rhodes` was louder at low velocity than at full (C4, a 0.15 s note, dB against 1.0: 0.2 +1.4, 0.4 -1.0, 0.6 -1.8,
  0.8 -1.1; C3 read up to +7 dB). The jRhodes3d samples are each normalised near full scale, so a soft layer reads 2
  to 7 dB hotter than the hard one, and the voice had no velocity gain, only a 3 dB slope inside a layer. Each layer
  is now matched in level to the key's hardest layer, so a layer change is timbre only, and the level follows the
  played velocity over 30 dB (new param `vel_db`, 0 for one level): 0.1 -30.7, 0.2 -21.7, 0.4 -12.6, 0.6 -7.2,
  0.8 -3.2, 1.0 0 dB at C4, within 1 dB of that at C3 and C5. Full velocity plays as before; softer notes in a song
  that used rhodes come out quieter. (ledger:M310)
- `emily` read one level from velocity 41 to 80 (one recorded layer of its 4, no level inside a layer). A layer now
  plays as recorded at its top velocity and falls toward the layer below's recorded level at its bottom: at C4,
  0.2 -13.6, 0.4 -8.2, 0.6 -3.2, 0.8 -0.2, 1.0 0 dB (was -10.8, -2.5, -2.5, +0.2). The samples are unchanged, and no
  layer is added. Where a hard layer was recorded no louder in RMS than the one below (a hard pick's short spike,
  on many keys from Eb3 up; its peak is still 5 to 6 dB higher), it stays as recorded.

### Level per velocity in voice_help; analyze_sections on a window render

- `voice_help(name)` adds a line with the voice's level per velocity: one short note at 0.2, 0.4, 0.6, 0.8 and 1.0,
  RMS in dB against 1.0, and the step between each. Velocity is not linear in level: x1.3 / x0.78 moved a balance
  +2.2 dB, not +4.3 (bossa_gilberto_studio), so read the dB off the curve and make a balance change in one step.
  Probed at C4 (or the middle of the voice's `range`), cached per voice file; a kit says it is silent there.
- `analyze_sections` on a window render (a `bars=[a, b]` render, in `renders/windows.json`): sections outside the
  window are refused with where the file starts ("starts at song bar 69 and holds bars 69-92 ... bars 1-4 of this
  file are [69, 72]"), and the default blocks follow the window instead of starting at bar 1.

### Blind tests: real or fake per clip, climb back to one note, score the window

- `exam_check(choices=, question=)` and `phone_exam` read the exam's one overall choice. On pairs labelled 1A, 1B,
  2A ... whose real side moves between pairs, a choice that asks which side is real ('A is the real band' / 'B is
  the real band', or bare 'A' / 'B' under a "which is real?" question) fails: it has no answer. A side choice about
  anything else warns. Choices that name no side ('pair 1', "can't tell", 'A lot'), a single pair, or a real side
  that never moves pass. `phone_exam(check=False)` still warns in its reply. From Nate's note on metal round 3.
- `sound_compare` says when the two sounds differ in length or one holds a release tail and no window was given:
  statistics over time count the whole file, so pass the window the person heard.
- blind-tests.md: ask "real or fake?" for each clip of a pair; after two band-level rounds the person still wins,
  climb back to an isolated note of the part they name; score the window, not the file.

### A mimic chord's first notes start fast

- mimic's attack map (every built-in profile and harp-like ones have one) ran on librosa.stft and istft, and
  librosa's first stft in a process costs seconds (its lazy imports and numba). A deck's first bars paid that on their
  first mimic note: 8 to 10 s of CPU for a first 8-note harp chord, 2.1 to 2.3 s now. The attack map now runs on a
  numpy STFT of the same window, padding and overlap-add (within -300 dB of librosa's). Each note also builds its
  time grid once rather than per partial, and the notes of a chord share their FFT frequency grid: the warm chord is
  about 4% faster. The sound is unchanged: the 8-note chord is within -327 dB of before.
  (ledger:M302)

### Mimic takes stay within 3 dB of each other

- A mimic note's random variation is seeded by its pitch, velocity and gate, and one velocity could draw a take 8 dB
  louder than its neighbours, every repeat of it as loud (cs80_lead D5 vel 90). Two causes: on a note shorter than
  the slow wanders, the "unit std" swell, shimmer and vibrato curves are short pieces scaled up, so their means became
  level offsets; and a steady tone met the per-note room IR in or out of phase. The note's level while the key is
  down is now held within 1.5 dB either way of the same note without the random parts, room included
  (`mimic.LEVEL_CAP_DB = 3`). Brightness, partial timing, swells and the room's colour still vary per note.
  Violin D5, vel 84 to 93, 0.3 s: 8.4 dB spread per unit velocity before, 3.2 after.
- The render summary names a mimic take that still sits more than 6 dB over the takes within 2 semitones of it
  (per unit velocity), with its velocity and first time, and says a velocity a step away draws another take.
- A change to mimic.py now clears the render cache of mimic tracks, as one to the other synthesis files does.
  (ledger:M301)

### An empty stem group reads silent in analyze_structure

- `analyze_structure(source='render')` printed a stem group with no tracks (no 'vocals' in the stem map, say) as all
  '9' and listed it among every section's loud stems: its rows were scaled to their own top, and digital silence was
  its own top. A row now reads '.' wherever it is under -100 dB on the features' level scale (silence reads about
  -117), and such a stem never counts as loud. The same floor applies to the stem rows of the cmp level maps.
  (ledger:M296)

### analyze_grid: the harmony votes on bar 1 two beats apart

- A snare on 2 and 4 reads the same under a two-beat shift, so on a four-on-the-floor house track the drum stem put
  bar 1 two beats late (celestial_resort_1024: drum stem 0.916 s, the bass and chords change on beat 1 at
  0.0687 s). `analyze_grid` now reads the bass root per beat (from the reference's bass stem when the source is the
  reference and it has one, else the low band of the source, plus its chroma turns) and counts the changes on each
  beat of the bar. The candidate two beats away wins with 1.5x the changes on its bar lines and at least max(3, 10%
  of the bars) more; under 3 changes in all, harmony does not vote and the pick stays. One line says it:
  "downbeat: drums say 0.9157 s, harmony says 0.0585 s (changes on beat 1 in 61 of 131 bars, on beat 3 in 15);
  using 0.0585 s" on that track's drum stem, and on a move the "bar 1 starts at" line and the chosen candidate are
  marked. (ledger:M312)

### The tell ledger

- `tell_add`, `tell_heard`, `tell_measure`, `tell_judged`, `tell_status`, `tell_show`, `tell_check`: a fault the
  person keeps naming in a sound is logged per song in `<song>/notes/tells.jsonl` (append-only, the Sound
  Engineer's prototype shape from the metal song), with their words, where they said them, and a measured gap each
  round. `tell_measure(reply=)` pulls the number from a `sound_compare`, `region_measure` or `sound_zoom_diff` reply
  or a saved report. `tell_show` flags NOT MOVING on an open tell named twice or more whose gap did not shrink
  between its last two measures, or that was never measured. Closing is refused without a passed verdict after the
  last measure, and `tell_check` names unknown ids, times out of order and malformed rows with what to do next.
- `tell_import(project, path)` adopts an existing tells.jsonl into an empty ledger. A song whose log is already at
  `<song>/notes/tells.jsonl` (the metal seed) is read in place.
- recreate.md: run `tell_show` before every round, log a measured row per open tell each round, and give a NOT
  MOVING tell a different method.

### Jobs waiting in line cost almost nothing

- 23 `python -m ismail.machine run --wait` waiters used 8.4% of the CPU together while the machine was at 99%: each
  took its own 2 s CPU sample and ran nvidia-smi every 5 s. Now one CPU sample (10 s) and one GPU reading (5 s) serve
  every process on the machine: the caller that finds the last reading old claims the sampling with a file next to
  it, and the others read the last reading meanwhile. A claim left by a process that died is cleared after 15 s.
- A waiter far back in line looks at the board less often (5 s near the front, up to 60 s further back), and the
  line is woken when a job ends, a waiter leaves it or the priority changes: the board touches `<board>/gen` and a
  waiter asleep in line looks at that file's time every 0.25 s. With 23 waiters over 30 s: 2 CPU samples instead of
  23, 6 nvidia-smi runs instead of 91, 26 looks at the board instead of 134, and the next job started 0.2 s after a
  job ended instead of 3.6 s.

### The machine board's lock serves callers in order

- With about 23 jobs waiting, a new `python -m ismail.machine run` could lose the race for the board's lock again
  and again and exit 75 after 10 s (one agent needed about 15 tries). Now each caller takes a ticket in
  `<board>/lockq` and the lock goes to the oldest ticket first; the callers behind it back off with jitter by
  their place in line. Waiters in line read the board without the lock and take it only when their turn may have
  come, and nothing slow runs inside it (the GPU and CPU readings come from their caches). With 30 callers taking
  the lock nonstop, every new caller got it within about 1.5 s in tests.
- A lock whose holder's process is gone is broken after 2 s instead of 30 s; a ticket whose owner died or passed
  its deadline is skipped. The lock waits up to 30 s (was 10 s). Exit 75 still means "not started, nothing
  registered", and the lock's message now says what to do: retry in a minute or `run --wait 30m`.

### The kit page

- `docs/KIT.md` (and `docs/kit.json`, the same data): every public voice and effect by family, with its type, what
  it is modeled on (an instrument type), its ear state and the exam or ledger row that judged it, its range, its cost
  and the gaps open in each family. Generated by `python -m ismail.kit` from showcase.json, presets, each voice's
  INFO and mimic profile, fx.py and the live engine, plus the hand-kept rows in `ismail/kit_tables.json` (the ear
  state is data there: `ear` and `exam`); `--check` and a test fail when the page is stale or a voice has no row.
  The `kit` op gives the rows as text, by family or for one voice. From the Sound Engineer's inventory (ledger M295).

### A voice named with fn='perform' plays live as a performer (ledger:M284)

- A code voice with `fn: 'perform'` played as a performer in the studio (the whole part at once) but note by note
  live: each note replayed the gesture score from beat 0, so a deck sounded wrong (corr -0.09 against the studio
  render on a samba deck) with no warning. The live path now marks a performer by the studio's own rule
  (`instruments.performer_fn`: fn 'perform', or a module with perform() and no function named fn). A tiny gesture
  voice on bars 3 to 5 went from corr 0.971 and -12 dB to corr 1.000 and 0.00 dB against the studio.
- A kit pad whose voice is a performer cannot play live as in the studio (live renders a kit hit by hit):
  `live_track` and `live_load` now say so and name the pads, with the fix (a track of its own).

### MIDI out to synths and other apps, and MIDI clock

- `live_midi_out`: a live track's notes (or a deck's part) to a MIDI output and channel while the set plays: a
  hardware synth, or a loopMIDI / IAC port into Ableton or FL Studio. Each note goes out when its audio would reach
  the speakers (the engine's clock, its lookahead and the output's latency), transposed with its deck, kits on
  channel 10. `audio=False` takes the track off the speakers and the render workers; `offset_ms` per port meets a
  synth's own delay. Unroute, a removed track, a stop and the set's end send each held note's note-off and All
  Notes Off (CC 123); closing a port resets it.
- `live_midi_clock`: MIDI clock out at 24 pulses per beat at the set's tempo, pulsing at once so the receiver locks,
  Start right before the first pulse of the next bar line (or the bar's Song Position Pointer then Continue, with
  `position=True`), Stop when turned off and at the set's end.
- `midi_ports` lists the MIDI inputs and outputs; `midi_play` plays a song's notes (or some bars) to a port in real
  time from the command line, with clock if asked, to try a synth without a live set.
- The sender runs on its own thread: it sleeps to about 1.5 ms before each message, then spins. On a loaded laptop
  (90% CPU) clock pulses went out 0.03 to 0.12 ms late at the median, under 2 ms at the 99th percentile. No new
  dependency: winmm on Windows, mido with python-rtmidi elsewhere (the error says what to install).

### What a deck costs, before it plays

- `deck_cost(project, bars=, bpm=, workers=)`: the deck's first bar loaded on a silent engine in the op's own
  process (no `live_start`, no workers, no device, nothing played), warmed as a started engine is, and timed: each
  track's render seconds for the bar and its real-time factor, its slowest event, its first note, its effects' ms
  per mixer block; the total, the wait for the first bar with the engine's render workers, and the mixer's load.
  The reset gap after a `live_load` was mostly that first bar, and the agent could not see it coming. (ledger:M293,
  the Live DJ's stream set of 10-22.)

### Frozen tracks, and placed audio on decks

- `track_freeze(project, tracks, into=)` renders tracks into one audio track a live deck can play: their mix as
  rendered (effects, faders, pans, automation), without the buses and the master chain, bar `a` at 0 s of
  `sounds/frozen_<into>.wav`. Options: `originals='mute'|'keep'`, `bars=[a, b]`; freezing into the same track
  again replaces it and lifts the mutes it no longer needs. A send or a bus every frozen track shares comes over;
  the reply names what did not. (ledger:M292, the Live DJ's stream set of 10-22.)
- `live_load` now carries audio tracks (placed clips and frozen tracks): the built-in `placed_audio` voice reads
  the song's own files by song time, one bar at a time, sample for sample with a studio render. Placed audio on a
  track that also has notes still stays behind, and the reply says so.

### Picture rounds from every song on one server

- The exam page server on 8871 (the only port the phone's `/eye/` reaches) served one folder, so a second song's
  picture round 404ed while another song's exam was up. Now `exam_picture_round` registers its round in a registry
  file (`~/.ismail/exampage/rounds.json`, or `$ISMAIL_EXAMPAGE_ROUNDS`) that the server reads on each request, after
  its own folder. A name another song's round holds gets the song's name in front (`/eye/songb-round2`), and the op
  returns that URL and phone link. It reads the round back through the server and, when it can't be served (no
  server would start, or the running one is older than the registry), says why instead of returning a link that
  404s. Old URLs and `python -m ismail.exampage serve <folder>` work as before; `python -m ismail.exampage register
  <round folder>` registers a round by hand. `/health` now gives the server's folder and `"registry": true`.

### Live: tempo changes

- `live_tempo(project, bpm, at='next_bar')` changes a running set's tempo at a bar, with no restart and no gap.
  Clips are in beats, so every part stays on the beat: the notes from the change bar on render again at the new
  tempo while the ones before it play up to it. When those renders cannot be ready in time, the change moves to a
  later bar and the reply names it. The reply says the bar, which tracks render again and which effects follow.
- One tempo map drives every beat and sample conversion: queued moves, fx changes, ramps and deck changes after
  the change bar move with it, and `live_status` lists the changes still to come.
- Beat-synced effects follow at the bar: delays in beats, gates, `every_beats` ducks, filter, phaser, flanger and
  tremolo LFOs in beats. Played keys keep their sounds; nothing of theirs renders again.
- A recording's `.json` lists each change as `[bar, bpm, seconds into the file]`; `live_listen` keeps to one tempo
  per window and says when it left bars out.
- The engine's clock (op `clock`, which `listen_heard` places words with) gives the tempo at the beat heard, not
  at the mix position half a second ahead, so words land on their bars across a change.
- Known gaps: `ramp_beats` is refused (queue step changes at successive bars), echoes already in a delay jump
  once, a note held across the change keeps its old render, and `@ms` offsets in clips convert at the tempo when
  queued.

### Chat commands by rule, votes and credit for requests

- `chat_commands(lines)` takes chat_watch's lines and handles `!request <text>` and `!vote <id>` the same way every
  time, by each venue's rules: a request is queued, refused (too many waiting, too soon, too long, closed), or
  counted as a vote when one like it is already waiting ("drum & bass" and "drum n bass" are one). It returns the
  reply for each line for the MC to say, and acts on a chat line once however often it is handed in. `!vote a` (a
  vote of the MC's own) is left to the MC. `!love` counts for the request that plays (request_next shows the
  count), with no reply. One `!vote` or `!love` per viewer per `reward_s` seconds (60), so the reward channel cannot
  be spammed.
- `venue_rules(venue, ...)` sets the rules per venue or for every venue ('*'): requests, votes and loves open or
  closed, waiting requests per viewer (1), seconds between one viewer's requests (600), duplicates as votes, the
  longest request (120 characters), one vote or love per viewer per `reward_s` (60), the owner and mods exempt.
- Votes move a request up the line (more votes first, then first asked); `request_vote` counts one that came
  another way.
- `request_credits(ids, path)` gives the credit lines for the viewers whose requests shaped a song (name, where they
  asked, what they asked for) and adds the missing ones to its CREDITS.md under "Requests from chat".

### A request queue for streams

- `request_add(who, what, venue, note)` puts a chat request in one queue and says its place in line;
  `request_next` gives the DJ the first waiting one at a phrase end; `request_mark(id, status)` moves it to playing
  (the one before it is marked done), done, skipped or back to waiting; `request_list` shows the line with
  positions; `request_where(who)` answers "when does mine play?" with the place and, once requests have played,
  about how many minutes. The queue is an append-only log (requests.jsonl in the stream home), so it survives a
  restart and an MC and a DJ in separate sessions see the same line. An unknown id names the ids there are; a bad
  status lists the valid ones.
- Each request keeps the UTC time of every state it entered (queued_at, playing_at, done_at, skipped_at), shown by
  `request_list` and `request_where`, so request-to-air time reads straight off the queue. When one starts playing,
  the stream's `now` line says whose it is ("request from @name: now playing ..."); `on_screen=False` leaves it.

### Fewer MCP tools for harnesses with a cap

- `ismail mcp --roles dj,mc` or `--shelves live,stream` serves only those roles' or shelves' ops (the ones
  `ops_find` already uses), always with `guide` and `ops_find`. `--list-roles` prints each role's and shelf's tool
  count without starting; `--max-tools N` refuses to start over N and names the sizes; an unknown role or shelf is
  refused with the valid ones. `ISMAIL_MCP_ROLES`, `ISMAIL_MCP_SHELVES` and `ISMAIL_MCP_MAX_TOOLS` do the same for a
  harness that cannot pass arguments. On a filtered server `ops_find` marks the ops it does not serve.

### The stage on a stream: an on-air page and GPU window capture

- `?onair=<camera>&fps=30` on a stage page shows that camera full frame with no UI, drawn at a steady rate, the
  pointer layer included, in a window titled "ismail on air <scene>". `stage_on_air` now also tells on-air pages to
  cut; other pages, the headset's included, ignore it.
- `stream_visual(capture='auto'|'wgc'|'gdi')`: `auto` captures a window with Windows Graphics Capture (ffmpeg 8
  gfxcapture) where ffmpeg has it, so a WebGL page drawn on the GPU comes through, covered or not, cropped to a
  browser window's web content. `wgc` asked for by name is refused without gfxcapture. With `auto` the visual page
  now draws on the GPU instead of in software; `gdi` is the old grab, and the manual fallback on a hot GPU.

### MIDI files in and out

- `midi_write`: the song, some tracks or a window of bars to a type 1 .mid any DAW opens (a track per part, tempo,
  meter, markers, nudges to the tick, kits on channel 10). `midi_read`: lists a .mid, then writes the parts you pick
  into tracks at any bar, optionally quantized; it warns on tempo or meter differences and on kit pads a GM drum file
  would leave silent. Written with no MIDI library; checked against mido on 40 files from other programs.

### Older downloads on the phone

- The Downloads section shows the last 4 as before, with an "Older downloads (n)" button under it that opens the
  rest, 10 more per tap, and "Show fewer" to fold it back. The phone server keeps the last 200 offers across
  restarts; `GET /api/offers?skip=&n=` pages them newest first, and the state carries `offers_n`.

### YouTube chat in realtime

- YouTube chat is read with streamList: one call held open that hands over each message as it is written, instead of
  a list call every 10 s. It reopens hourly with a fresh login, lets go when the broadcast ends, and falls back to
  polling if YouTube refuses the stream. `venue_set(reader='poll', poll_s=...)` picks polling. It needs grpcio and
  protobuf (the `stream` extra); the messages are built at runtime, nothing is generated.

### The stream's visual page, drawn from the set

- `stream_visual(mode='page')` streams a page that moves to the set: the master's level and 16 bands, the engine's
  beat, and each track's notes as they sound. It reads the engine's local stream and its clock and onsets ops, never
  the mixer.
- `stream_scene` sets what it draws with raw numbers (bars, ring, line, pulse, particles, per-track hits, text,
  grid; each driven by the level, a band, the beat or a track), live with no blip; a wrong field says which fields
  the layer takes. `stream_page` reports the page's address and what it sees now.

### Live streaming to several sites, with every site's chat

- A stream server (`stream_start`) sends the live set to Twitch, YouTube, Kick, Owncast or any RTMP site at once:
  the master and a picture drawn from it (`stream_visual`: cqt, spectrum, waves, scope, a still or a captured
  window, with text lines that change live, `stream_text`) are encoded once (NVENC when the GPU has it) and copied to
  each site by its own process, so one site dropping never stops the others. Engine restarts carry silence, not a
  drop. A `file` venue records every set.
- Every site's chat lands in one numbered log: `chat_watch` (a standing watcher, like phone_watch), `chat_read`,
  and `chat_say` (one venue, every venue, or a reply where the person wrote). Twitch reads over EventSub (or
  anonymously, read-only), YouTube through the Data API within its quota, Kick over its public websocket.
- Venues (`venue_set`, `venue_list`, `venue_remove`) hold no secrets. The person types logins and stream keys in a
  terminal (`python -m ismail.stream login|key <venue>`); no op ever returns one, and logs blank them.
- `stream_go` needs the person's words each time it goes live on a public site, and logs them.
- A practice venue (`platform='fake'`): a local chat page and a scripted test viewer.
- `references/live-streaming.md`: the setup, going live, chat manners, the DJ and MC roles, chat games.

### A cut takes the old deck off air at once

- `live_transition(style='cut')` ignores `bars`: the old deck's tracks stop and it goes off air (cued) on the cut's own
  boundary. It used to stay on air at fader off for the default 16 bars, costing the mixer and refusing a load onto it.

### One listener's taste is no longer a rule in the playbook

- Twelve places in SKILL.md, live.md, composition.md, music-video.md and instruments.md stated one person's taste
  as a rule for every set and song (derived melodies only, a new hook every 2 minutes, an effects pass with a
  riser into every section, a climax in every form, a VHS look on every video, and more). They now read as
  examples, scoped to the style they came from or to when the person asks for them.

### Moments marked while the song plays

- `feedback_page(moments=True)` puts a time strip with bar numbers and a "Mark this moment" button (or the M key)
  under the first audio item (`moments='<label or path>'` picks another). Each moment gets tick boxes (love this,
  too much, which part, in the person's own part names) and their words.
- The served page saves moments in the round (`moments.json`); a shared copy keeps them in the browser and puts a
  "Moments:" block in the message, which `feedback_paste` reads back.
- `feedback_read` and `feedback_wait` list the moments in time order with their bars (a windowed render counts from
  where it starts), and a moment that names a quality gets its measure step at that time.

### Recreating and arranging: lessons from four metal recreations and an electro house vocal

- recreate.md: count a tab's real bar starts through meter changes; render both readings of an ambiguous part;
  snap click-less drums to the record's own onsets; replace the instrument in fit loops; time drafts stem by stem;
  judge tremolo guitar by chroma; score the window, not the file.
- composition.md: a clear buildup with the voice on the drop, a payoff line held until the story earns it, where
  phrase halves land, spoken lines on the stressed syllable, filling a voiceless stretch with what was said, and a
  high robot voice from a vocoder carrier.

### Render reads the true peak; the noise riser stays under it

- render's master line gives the true peak (4x oversampled) beside the sample peak, and says when it is over
  -1 dBTP: a sample-peak limiter let a mix read -2 dBFS while it was +0.2 dBTP, and an mp3 of it clips.
- The noise_riser preset's band-pass tops out at 10.8 kHz instead of 12.8 kHz, which halves its overshoot
  between samples.

### A song's backup moves its project whole or not at all

- `backup.set_aside` (a song build's backup of proj/) renames proj/ in one step. When something holds it open (a
  terminal working inside it, on Windows), nothing moves and the error says what to close. Before, the move fell
  back to copy-then-delete, emptied proj/ around the locked folder and left the reference stems in a backup that a
  later rebuild would trim.

### Live decks: a new song starts clean, and removing a deck no longer stops the set

- `live_load` onto a deck that held a song resets its strip (fader, eq, filter, transpose) even when its tracks
  were removed one by one, and says what it reset. A blend's bass kill had played the next song with no bass.
- Removing a deck while a block was mixing stopped the mixer for good (the heard bar froze and underruns climbed).
  The mixer now reads the decks it started the block with, and a block that fails plays silent and is named in
  the next reply instead of ending the set.
- `live_status` says whether each off-air deck is asleep (costs nothing), ringing out, or still playing notes
  unheard.

### Live sets: helpers compose, and the mixer's cost is counted

An electro set dropped out for most of its length. Helpers measured references while it played, a priority loop
cost a core, one deck carried hundreds of per-beat effect ramps, and a deck taken off air was still mixed.

- The live reference says helpers started during a set compose only (notes, patches, effects, from the set's own
  kit, within a track and effect budget, at most two at a time); measuring happens before the set.
- A known song goes on air before any helper starts, and a sleeping Bluetooth speaker is named at once.
- The mixer budget counts automation as well as tracks. A deck off air after a transition is removed or reloaded.
  Its strip is set back after a new load (a bass kill carried over). Heavy ramps are cleared once a deck lands.
- Your own live calls are batched rather than polled, and priority tricks never stand in for fewer jobs.
- A mixer that stays high on a light deck after a long set means restart with `workers=2` (a 20 s gap): one set went
  from 79-105% to 28% on the same deck. Sets on a shared 8-thread machine start with `workers=2`.

### One call to look before a draft goes out, and notes that say where to look

Over one song's rounds an agent ran render about 43 times and sound_compare about 30, and never the stored comparison,
its worst bars, a zoom diff or a region measure, though the playbook names them all. By feedback time the playbook
was too far back to recall them.

- `ear_pass` runs the stored comparison with the reference, ranks the worst bars, and for each of the top ones names
  the band that differs most (with the `region_measure` call that sizes it), zooms it step by step and writes a crop
  of yours and one of the reference at that bar to read side by side. The render reply's "Next:" now points to it.
- A `sound_compare` alone no longer counts as "compared with the reference" in the render reply; `ear_pass`,
  `compare` and `cmp_run` do.
- `feedback_read` (and so `feedback_wait` and `feedback_paste`) turns each note that names a quality ("muddy", "too
  loud", "fake", "out of time") into its measure step: the tool for that kind of word, with the window filled in
  when the note gives bars or a time, and the part when the note names one. Words like "dark" or "dry" count only
  as "too dark", "too dry": alone they are taste.

### Part names are checked with the person by ear

A listener said the bass overpowered the track. They meant the answering bass; the agent turned down another one it
called the talker, and three rounds went by before anyone noticed. Nothing checked that both used the same names.

- `feedback_page(parts=True)` (or a list of tracks) adds "Name the parts": each part alone and in the mix at the bars
  where it stands out most (cut from the latest `render(stems=True)`), with a box for the person's name for it. It
  works on the shared copy and comes back through "Copy my answers" and `feedback_paste`.
- `feedback_read` shows their names, and flags a note whose words could mean several parts ("says 'bass', and 3
  parts could be it") from the track names, the stem map and the instruments, unless the note names one of them.
- `lexicon_note(track=, heard=<round>)` keeps their name for a part, confirmed only when it matches what they typed in
  that round; `track=` alone is marked as not confirmed.

### A listener who is not at this computer can answer, by voice too

A friend answering rounds from elsewhere could not send their answers: the feedback page runs on this computer
only, the session built its own hosted page, and its Send failed twice (a view-only share cannot save).

- Every feedback page has "Copy my answers": plain text, numbered as the questions, to paste into any chat.
- `feedback_page(share=True)` also writes a standalone copy (`<round>/share/`: index.html and the clips beside it) to
  host anywhere. It needs no server and no write access: typing and voice notes stay in the listener's browser, and
  "Send my answers" sends them as a message (the share menu with the voice notes as files, or a copy to paste and the
  voice notes to download).
- `feedback_paste(project, round, text, files)` reads that message into the round: each numbered answer under its
  question, each voice note (`q<n>_<time>.webm`) as a clip transcribed in the background, then it counts as their
  Send, so `feedback_read` and `feedback_wait` work as for a page answered here.

### Shared pages say what made the song

A listening page sent to a friend had no word on what made the song or where to find it.

- `credits(format='footer')` returns the footer for a page people may share, as text and HTML: made with ismail, the
  link to the site, and how the sound was made by counts only (parts modeled on measured recordings, designed,
  playing recorded audio), with no names and no paths. It writes nothing.
- `feedback_page` pages end with it (`footer=False` leaves it off).
- SKILL.md step 8: a page you build yourself for someone who may pass it on ends with it.

### The effects pass is part of the first draft

Two sessions (an electro track, a drum and bass recreation) handed over whole drafts with no effects pass until the
person asked for one: the loop ended its build at "ear candy" and never named the pass.

- SKILL.md loop, step 5: the effects pass in the first draft: a transition into every section, what changes at each
  phrase end (fill, filter dip or sweep, drop-out, delay or reverb throw), the ducks; with a reference, zoom on a
  phrase's last bar against the one before. The Session Sheet gets a `Moves:` line.
- A full render of a song with three or more tracks and no automation lane or no transitions part says so in one line.

### Balance against the reference in every render with stems

A recreation's second bass was far too loud through three drafts, and nothing said so: the only balance check
(`levels_from_ref`) ran once, on draft 1, and averages over the whole song.

- `render(stems=True)`, with a reference that has stems and a stem map, prints each stem's share of the mix against
  the reference's per 8-bar stretch (quiet stretches left out): how far off over the song, the worst stretch, the
  loudest track of the stem there; a layer the reference has in a stretch and yours lacks (often a part filed under
  another stem); a stem that plays where the reference's is silent.
- Then one line on the master: a limiter alone is not a mastering pass; balance first, then `references/mastering.md`.
- SKILL.md step 6 and mastering.md section 0 say the balance comes before loudness and the master.

### Synthesis unless the user asks for samples

A session cut a reference's kick, snare and shaker into the song after the listener said the drums sounded too
synthetic. The skill and `sound_extract` both left that door open.

- SKILL.md, Non-negotiables: songs are made with synthesis (sprites, measured voices, mimic profiles, the modeled
  library voices). Samples (slices of the reference, imported one-shots, the sampled library voices) go in only when
  the user asks for them in words. "Too synthetic" means measure the hit against the reference's and rebuild it.
- instruments.md: the sampled-voice and sampler routes are for when the user asked for samples. recreate.md: an
  extracted hit is a fitting target, never a part.
- `sound_extract`: its description no longer says "use it", and its reply says the hit is a target to fit.

### Transitions, speech on a rhythm, is the voice heard (ledger:M249, M250)

Three pieces built in a song and proven there.

- New library voice `transitions` (fx family): the note picks the effect, C1 an impact, D1 a noise uplifter, E1 a
  downlifter, any other note (F1) a reverse crash, and the sweeps last as long as the note, so a 2-bar note ending
  on the drop is a 2-bar riser. Designed by ear, no recordings. It plays live sample for sample.
- `sound_speak` saves the engine's own word and phone timings beside the sound on Windows
  (`sounds/<name>.timings.json`; other systems say timings are not available). New params `rhythm=` and `bpm=`
  put each syllable on a written 16th: `rhythm='night 0; shift 2; engineer 12 14 16'`, one position per syllable,
  `-` skips one. Each syllable is cut where the next starts (a syllable starts at the last consonant before its
  vowel), and the reply says where the sound starts so the stressed word lands on its beat. The timings come from
  SAPI's events because a speech server's word starts ran early and cut syllables.
- New op `analyze_voice_heard(project, voice, bars=None)`: after `render(stems=True)`, the voice stem against the
  sum of the other stems per band (body, words, consonants, air) over the frames where the voice speaks, the
  loudest other part in each band, and a one-line verdict naming the band that masks it. No transcription.

### Replies that read the live state: the reference, a Send button, "hear it" (ledger:M245, M246, M247)

- A first-session test kept its reference in a separate analysis project, never compared a draft with
  it, and called a draft "measured on" the reference. render's reply had no word about the reference. Now, when
  the project has a reference, render prints one line: compared with it never, or not since this render with the
  last comparison (compare, cmp_run and sound_compare against `ref` leave a receipt in `renders/compared.json`);
  when the song's ref/ holds audio the project does not use, it says `project_set(reference=)` makes the reference
  ops reachable. `project_set(reference=)` and `project_new(reference=)` say what to run next, with the caveat
  that the part carrying the reference's identity is checked on top, never averaged down to its stem.
- `ops_find("the snare sounds dated, make it sound like the genre")` returned phone_sounds and sound_speak (the
  word "sound" in their names). analyze_kit, sound_extract, instrument_fit and sound_compare now have "Reach for it
  when" sentences in their descriptions, as project_set and compare do for a reference that arrives as
  inspiration; both queries are tests.
- `instrument_help(type='kit')` and `type='code'` say that note names follow C4 = 60, so C1 is MIDI 24 where GM
  drum charts say 36 (a kit voice mapped by GM numbers played a rim click on every hit).
- The feedback page saved on every keystroke and had no way to say "finished", so an agent read and answered a
  half-typed note. Every round now ends in a Send button that writes `done.json`; typing or recording after it
  marks the round changed after sending (the earlier send time stays) until Send is pressed again.
  `feedback_read` opens with SENT or STILL WRITING and, when not sent, "not sent yet: don't act on these answers".
  New op `feedback_wait(project, round, timeout_s=180, after=)` waits for the Send and returns the answers, or at
  timeout what is typed so far and how to call it again; `feedback_page`'s reply points to it and never asks the
  person to type "done". A page server started before this has no Send button: the replies say so and how to
  restart it.
- Rounds given a song's `proj/` were written inside it and lost when the song's build regenerated `proj/`. They now
  go in the song folder (`songs/<slug>/work/feedback/<round>/`); a round already in `proj/` stays where it is.
- Drafts went out as file cards the person did not find. render (after an mp3 or a named render) and sketch now end
  in a short "hear it" block built from live state, by file reads and local requests of under half a second: the
  file's full path; the phone page and when the phone last checked in (the phone server's `/health` now lists its
  devices); the stage when the person is in the headset; a live engine for this song; this project's feedback
  round; then one option chosen from that state (a standing choice in `project_set(listen_pref=)`, then the stage,
  an open feedback round, a phone seen in the last 5 minutes, a live engine, else play_file or a feedback page).
  It offers; whether a draft goes on a page or hands over as a file stays the agent's call.

### Skill: lessons from a first-session test, and one consistent playbook (ledger:M245)

- A test song came out bland: it matched the reference's averages (a riff turned down 10 dB to a stem's level),
  wrote a stock chord loop over a reference with one pedal, kept a generic snare, and never compared a draft with
  the reference, because every reference op was routed through recreate.md. The skill now says, where the decision
  is made: a reference that arrives as inspiration is set on the song and compared before every draft (a new row,
  the loop's listen item, a Reference line in the Listening Report, recreate.md); the Session Sheet's harmony is
  measured from the reference or the genre, a pedal included, and names the reference's signature, built first and
  pushed; the part that carries the identity is checked on top where it enters, not averaged; "design it" still
  means measuring genre tracks yourself; a stem is a hint and the full mix the target; genre drums come from
  measured kit pieces; C1 is MIDI 24 in ismail (GM charts say 36).
- One rule against guessing in Non-negotiables: a cause, a fact about the reference or a reading of the person's
  words is a guess until one check backs it, the check is named, and an unchecked cause is never acted on twice.
- Handing a draft over: the person always gets something to open and hear, the agent follows the "hear it" block
  in render's and sketch's replies (the VR stage included), the person's way of listening is asked once and kept,
  and a feedback page is read after their Send (`feedback_wait`), never a typed "done".
- Coherence pass: the loop reads as a checklist, not a sequence; the guide's lines tied to "step 8" or "step 0"
  name the moment instead; duplicates folded (the mp3-and-question rule into the loop, the measured-numbers rule
  into the guess rule, the eye exam in instruments.md into a pointer); an exam page is never handed over as a file
  path while a song file is.

### Skill: yt-dlp is a Python library too

- `recreate.md` section 0 says yt-dlp is a Python library as well as a command, so a script can fetch several
  references at once.

### Skill: one reference for helper agents (ledger:M243)

- A first session took 15 minutes to its first sound while one agent built every voice in turn. Helpers were
  mentioned only inside the jam and stage-takes guides. `references/helpers.md` now covers them for any piece:
  quick mode by default and a crew when it pays, sound first while helpers build, who owns what (only the main
  agent talks and plays sound), the brief, the shared machine (helpers use the parent's `ISMAIL_SESSION`, one heavy
  job per crew), cheaper models for short narrow tasks with the person's choice winning, and working without
  helpers. SKILL.md (the loop, the table, the reference list), live.md, stage-takes.md, development.md and
  worktrees.md point to it.

### Skill: fetching a reference from a link

- A new session given a YouTube link had only "yt-dlp; YouTube may bot-wall" to go on, improvised other clients
  and a browser, and got the computer blocked. `references/recreate.md` section 0 now gives the two commands the
  sessions that fetch references fine use (find the upload and its SOURCES.md fields, then fetch the audio into
  `ref/`), recording it as it plays (`listen_start` on the speakers) when there is no download, and one line for
  a failed download: say so, offer the recording or a file, no retries. A row in SKILL.md's
  table and the instruments guide point to it; `setup.md` installs yt-dlp and deno after one yes.

### Phone: a linked .md, .csv or .log opens as text

- A panel link to a Markdown plan downloaded on the phone instead of opening (the server sent types it did not
  know as `application/octet-stream`, and a phone has nothing to open a `.md` with). `.md`, `.csv` and `.log` are
  now sent as `text/plain; charset=utf-8`. Test: the type table in `tests/test_phone.py`.

### analyze_tuning reads fast notes and their attacks (ledger:M231)

- Each note was read from a fixed 100 ms after its onset and needed 60 ms of steady part after that, so a fast run
  (a 16th at 104 bpm is ~144 ms onset to onset, and a plucked voice may end it ~60 ms early) read as zero notes:
  the passage a listener called badly out of tune was never measured. In fast runs the fault is in the attack
  itself: each fast plucked note starts 5-10 c sharp (the voice's pitch glide) and never settles.
- The skip now scales with the note: `min(skip_ms, 0.3 x its length)` (onset to the next onset, or to its level's
  30 dB fall), and the steady part it needs is `min(60 ms, 0.4 x its length)`, at least 25 ms. `skip_ms` keeps its
  meaning as the longest skip. Every reading needs 4 periods of its pitch in its window (a 25 ms window reads down
  to ~170 Hz); a note too short to resolve its pitch is counted ("too short to resolve"), never guessed.
- Notes are timed from the attack itself (the steepest rise in level near the onset), and a read ends where the
  next note's attack starts, so a short window never mixes two notes.
- The attack is read too, from 30 to 80 ms after the onset (less on a short note). The table gets an `attack`
  column (median cents of the attack over the steady pitch), and when most attacks sit more than 5 c off, the
  verdict adds "attacks start N c sharp ...: shorten the voice's pitch glide". A note too short for a separate
  steady part is read from its attack on, marked `attack only` in the worst notes, and still counts against the
  grid, so a fast run is never silently empty.
- Tests: a run of plucked 16ths at 104 bpm 30 c sharp is read (32 notes, +30 c; the fixed skip read none) and
  flagged; a run whose notes start 8 c sharp for 40 ms reads in tune with an attack of +8 c and is flagged on the
  attack; the 40 c glide on slow plucks stays out of the steady pitch and is now named as the attack (+16 c); the
  drum (tumba +46 c, conga -49 c), noise and grid tests are unchanged.

### analyze_tuning: every part of a render against the tuning grid and key (ledger:M231)

- A pitched part off the tuning grid went unnoticed until someone heard it: a salsa kit measured from records kept
  their tuning (its tumba sustained at F#3 +46 c, its conga at C#4 -50 c, a quarter tone off any key), and a nylon
  guitar's chords drifted 10-20 c string to string. New op
  `analyze_tuning(source='render', stems=None, tol_cents=15, bars=None, key=None, tuning_cents=None, skip_ms=100,
  worst=5)` reads every track stem of the last `render(stems=True)` (or the reference's stems with `source='ref'`, one
  part by `track:`/`ref:`/`sound:`/path, a whole mix by `render:mix`/`ref:mix`) and flags notes more than
  `tol_cents` off the grid.
- Per note it reads the steady pitch from `skip_ms` after the onset (plucks start up to ~10 c sharp for ~90 ms, a
  drum's strike is noise), from spectral lines: chords give one pitch per note, partials fold onto their note (a
  tumba's octave mode reads as the tumba), gliding notes and noise are counted and not read. Per part: pitched notes,
  median cents off the grid, spread (middle half), the count beyond the tolerance, the worst notes (seconds, bar and
  beat, note, cents, Hz) and one verdict: in tune; the whole part off (retune by N c); a few pitches off (retune
  that voice or split it onto its own track); or drifting note to note. A part with no steady pitch is skipped,
  never flagged.
- The grid is A4 = 440 Hz (ismail plays at A440), moved by `tuning_cents`; a reference `ref_retune` has not moved is
  read on its own measured tuning. With a key (`key=`, or the project's or the sketch's) the out-of-key pitches are
  listed as information, never as a flag.
- Listed in `guide`, `docs/reference.md`, the listening reference (run it after the first render of a song with
  pitched percussion or a voice in a new register, and before any exam) and the instruments reference.
- Tests: `tests/test_analyze_tuning.py` (synthetic stems: a part on the grid; tumba +46 c and conga -49 c flagged
  within 3 c beside an in-tune quinto; a 40 c attack glide inside 90 ms not flagged; a noise part skipped; the grid
  moved by `tuning_cents`; a reference read on its own tuning until retuned; errors).

### Worktrees: `done` takes a copy whose pull request was squash-merged

- `done <path>` refused a copy whose pull request GitHub had squash-merged: "is not merged: N commits are not in
  origin/main". A squash (or rebase) merge puts new commits on main, so the branch's own commits never land there,
  and the copy stayed until someone removed it by hand.
- `done` now lets a copy through on any of three tests, in this order, and says which one passed: its commits are in
  `origin/main` (as before); its files are the same as `origin/main`'s (`git diff --quiet origin/main <head>`: no
  work is lost); or `gh pr list --head <branch> --state merged` names a merged pull request whose last commit is the
  copy's last commit. The answer ends with `(its commits are in origin/main)`, `(its files are the same as
  origin/main)` or `(its pull request #N is merged)`.
- Real unmerged work is still refused: a commit made after the merge, a branch with no merged pull request, and a
  copy with no branch. The refusal says why the pull request test did not pass (no merged pull request, commits
  made after it, or gh missing or not answering). The checks for uncommitted and untracked files are unchanged.
- Limits: `list` still reads a squash-merged copy as unmerged (it does not ask gh per copy), so such a copy can show
  IDLE or BEHIND until `done` removes it.
- Tests: `tests/test_worktrees.py` (a squash-merged copy passing on its files, then on its merged pull request after
  main moved on; a commit after the merge, no merged pull request and no gh all refused). No test calls the real gh.

### Stage: a camera crew's tools (stage_on_air, stage_pointer)

- For the same talk on the stage (the user, 10-17: agents as a camera crew, a director calling the shots; "wherever my
  hands point, the camera view shows some kind of pointer ... funny"). `stage_on_air(scene, camera)` cuts to a
  camera: the monitor material (default `monitor_screen`) shows it live through `stage_surface`, and the cut is
  logged in scenes/<scene>/on_air.jsonl with its UTC time and an optional note; a camera the scene's manifest does
  not list is refused and not logged. `stage_on_air_log` lists the cuts with how long each stayed on air. A cut on a
  monitor that already shows a camera only changes the camera (no new render target).
- `stage_pointer`: a giant foam finger where a person's hand points (a body following the user live or playing a
  take), from the shoulder through the index fingertip to the first surface (2.5 m out when nothing is hit). With
  `when='point'` it shows only while that hand points (index straight, middle and ring curled, the hand 40 cm or more
  from the shoulder, held 120 ms), and grows in and shrinks away. It is drawn on a new layer, `CAM_ONLY` (editor.js),
  which the headset's eyes never enable and capture and surface cameras do, so the speaker never sees it and every
  camera does.
- Limits: one style for now (`foam_finger`); the pointing test reads the body's finger bones, so a body without
  finger bones needs `when='always'`. Only the first person following or playing is used unless `person` is given.

### Stage: screens in the room (stage_surface)

- For a talk given from the stage (the user, 10-17: a lecture hall with slides, a video backdrop, a teleprompter
  and a monitor showing the camera on air): `stage_surface` shows an image, a video, text or a live camera on every
  mesh of a named material, now, by an agent. The meshes draw flat and unlit (a screen glows; `glow` dims it) and
  get their own material back with `clear=True`; nothing is saved to the scene. A re-exported room gets its
  surfaces back; another scene starts with none.
- The surface keeps its mesh's shape: its aspect is measured from the UVs of its first triangle in world space (or
  `aspect=`), and images, video and text are drawn into a canvas of that shape, fitted `contain` (on `background`),
  `cover` or `stretch`; `px` is the long side. Text wraps to the width, `lines` at once, scrolling `scroll` lines a
  second from `line`. A camera renders at the surface's aspect with the camera's horizontal field of view, `fps`
  times a second, never drawing its own screen (a texture cannot be read while it is drawn into).
- Videos load as a whole file (seekable without range requests), muted unless `sound=True`. In a capture
  (`stage_capture` setup, timed with `at_song`) a video is seeked to the film's time every frame and a camera
  renders every frame, so the edit sees what the room showed. Files must be inside the scene folder; the op checks
  before sending.
- Limits: each camera feed is one more render of the room (keep `fps` low in the headset); a surface covers every
  mesh of its material, so screens that show different things need their own materials; which way up a picture
  lands follows the plane's UVs.

### Tests: two stage tests no longer depend on how fast the Windows runner is

- `test_a_recorded_move_thins_to_few_keys_that_play_back_within_two_cm_and_two_degrees` timed out on Windows CI
  (main after pr:261 on 10-15, and pr:262), and `test_smooth_glides_through_a_key_and_stop_halts_there` did the same
  on 10-12. Each time it was the run's first `node -e`: the script itself takes about 25 ms, and every later node
  call in those runs passed. The five stage test files that run page code in node now share `tests/nodejs.py`: the
  run's first launch is a warm-up of its own (`node -e 0`, up to 300 s, named in the warnings summary if it takes
  over 5 s), and a script that still times out at 60 s runs once more, with a warning. A script that hangs still
  fails.
- `test_listen_and_presence_ops` failed on Windows CI (pr:262 on 10-15, main at 50b115b on 10-17) with the note "heard by
  tester" but still `waiting`: the check that marks it handed runs on a timer thread 0.3 s after the note, and the
  test read presence after a fixed 0.6 s sleep. It now waits up to 30 s for the note to be decided, with a 2 s
  window to hand it over, and still asserts `handed`.

### engine: one reclaim folder at the root of the drive

- New `ismail/reclaim.py` (ledger:M229, the user's voice note: all reclaim folders go in one place at the root of the
  drive). `reclaim.root()` is `$ISMAIL_RECLAIM`, else on Windows the songs folder's drive root plus `_reclaim`
  (`D:\_reclaim`), else `songs/_reclaim`; when that cannot be made or written it falls back to `songs/_reclaim` and
  the op's report says so. `reclaim.move(path, what, group=, why=)` moves a file or folder to
  `_reclaim/<what>_<YYYYMMDD>/<group>/<path from the drive root>`, never overwrites (a numeric suffix), appends a run
  to that folder's `manifest.json` and adds or updates its entry in `_reclaim/README.txt` (other entries untouched).
  `reclaim.Run` keeps many moves as one run. `python -m ismail.reclaim` lists the folders with their sizes;
  `python -m ismail.reclaim move <path> <what>` moves one. Nothing in it deletes.
- `song_compress` moves the WAVs to `_reclaim/flac_<date>/<song>/` there (its per-file data kept in the manifest),
  and `ismail.backup` trims older backups into `_reclaim/backups_<date>/<song>/`, both instead of `songs/_reclaim`.
- `machine_disk` lists the reclaim folder's folders with their sizes, and the old per-song `songs/*/_reclaim`
  folders as before. The disk hint, the worktree cleanup message, SKILL.md and docs/reference.md name the new place.
- Tests point `ISMAIL_RECLAIM` at a temp folder for every test (tests/conftest.py); new tests/test_reclaim.py.

### skill: every song can be made again from its folder

- `skills/ismail/SKILL.md`: each song keeps a script that rebuilds it and renders its audio (`build.py`, `set.py`, and
  one written for a song taken out of a live session), with what it uses copied into the folder. So its renders and
  stems are disposable: kept are reference audio, recordings and takes, and upload masters; a WAV gets an mp3 before it
  goes, and cleared files move to the reclaim folder, never deleted by an agent (ledger:M228, the user's word).
- Step 8 (play it and ask): a person who cares about disk space is asked once whether WAVs are kept; the answer is
  a standing choice, and "no" means named renders with `mp3='only'`, older WAVs with an mp3 to the reclaim folder.

### phone: a panel left unsent keeps what you typed

- Typed text, dictated text and picks in a panel were lost when you left it (Later, another tab, a tap outside) and
  came back: the page rebuilt the panel from the agent's values, so Send went out empty. The page now keeps a
  panel's inputs when it goes away unsent and puts them back when you reopen it, on this phone across a reload too;
  a voice note that lands while the panel is closed still adds its words. An answer or Not now drops them
  (ledger:M227).
- Tests: `tests/test_phone.py` checks the page keeps, restores and drops them.

### A sampler with several sounds on one pitch; a phone now label that expires (ledger:M218)

- From the Live DJ's handoff (item 55, 10-16): a kit maps one pitch to one instrument, so the beatbox kit put each
  take of a drum on its own pitch and rewrote every note's pitch in turn, and the live loop started that cycle over
  every pass. A sampler now takes `'sounds': ['a', 'b', 'c']` in place of `'sound'` with `'select'`:
  `round_robin` (the default: the k-th hit of a bar of c hits in song bar b plays sound (b * c + k) mod n, so the
  turns carry on from bar to bar), `random` (seeded on the note's song beat and pitch) or `velocity` (each sound
  `{'sound': 'soft', 'vel': [0, 80], 'gain_db': 0}`; without ranges they split 0-127 evenly, softest first; sounds
  whose ranges overlap take turns). Each sound may carry its own `gain_db`.
- The pick is keyed on where the note sits in the song, as synth and drum seeds are, so a window render picks what
  the whole song picks. Live, a track whose sampler picks among sounds renders a bar per event and passes the
  deck's song beat to the worker, so a deck plays the studio's sounds sample for sample (`live_parity`). A looped
  live clip of whole bars carries the turns on from pass to pass as if the bar were written out again, repeating
  after `select_cycle` passes (round robin: the number of sounds; random: 8) so the render cache stays bounded.
- One `'sound'` plays exactly as before. `'select'` without `'sounds'`, both `'sound'` and `'sounds'`, a `'vel'`
  range outside velocity mode, and ranges given to some sounds and not others are refused with what to write.
  Render caching, provenance and the credits read every sound of a sampler.
- phone_now: a now label set by a script used to win over the engine's playing list until someone cleared it (on
  10-12 the page named a cumbia while the beatbox loop played). The label is now tied to the clips playing as it
  goes up and those that start in the next 2 bars (a label put up just before its music is queued), and expires
  when they all stop; the page then shows the engine's own playing list. `now_of=[...]` ties it to named clips,
  tracks or decks; `now_of=[]` keeps it up until replaced or cleared, as before. An engine that does not answer
  (a pause) leaves the label as it is.
- Limits: played keys (`live_midi`) render each key once and have no place in the song, so a multi-sound sampler
  on keys plays one sound per key (keys render at velocity 100 and scale, so velocity layers do not switch there). A looped live clip whose length is not whole bars repeats its first
  pass's picks. A label bound to clips expires on a chapter change that queues new clips without a new phone_now
  (the page falls back to the engine's list, not a wrong name).
- Tests: `tests/test_sampler_select.py` (the checks, round robin, random, velocity layers and overlaps, per-sound
  gain, a window render matching the whole song inside a kit, a deck matching the studio for round robin and
  random, a looped live clip carrying the turns on); `tests/test_phone.py` (a label expiring when its clip stops,
  one named by deck, and an unbound one).

### Governor: the macOS meter counts a job's compressed memory (ledger:M166)

- On macOS the job meter read a process's own memory as its resident size, and macOS compresses pages out of the
  resident set under memory pressure, random bytes too. In the test suite a `machine run` job holding 900 MB of
  random bytes read 844 MB resident at its peak and 812 MB a second later, so it stayed under the 0.85 GB OVER
  line and OVER MEMORY never fired there. Alone on the same runner it read 914 MB and fired. The meter always saw
  the job; it saw too little of it.
- `_mem_of` now reads the physical footprint on macOS (`proc_pid_rusage`, the number Activity Monitor shows:
  resident plus compressed and swapped out pages), which held at 908 MB through the same run. It falls back to the
  resident size when the call is not there. Windows (private bytes) and Linux (resident size) are unchanged.
- Tests: `test_the_cli_says_over_memory_in_the_jobs_own_output` is no longer an expected failure on macOS.

### analyze_melody: a held note reads as one note

- A held note could read as two notes of the same pitch. librosa's onset detector also fires where no note starts:
  on the click of a note that stops dead (an eighth held 60% split at +0.30 beat), and inside a held tone whose
  timbre moves (a wah fires every sweep). `melody()` kept every onset as a split, and the tail past it still read as
  pitched, since pYIN's 93 ms window smears the pitch past the cut, so it passed as a note of its own; the merge
  step after it only shortens notes and never joins two of the same pitch.
- An onset is now dropped when the pitch is the same on both sides (within 50 cents) and nothing re-attacks. A
  re-attack is a level rise of 3 dB or more out of a dip before the next onset (a gap, or a decaying note struck
  again), or a hard restart: the level holds and the onset stands 15 times over the onset strength around it (a
  legato retrigger of a flat synth note clicks). A note struck again at the same pitch stays two notes. The output
  format is unchanged (ledger:M226).
- Limits: a legato retrigger that barely clicks (a pure sine restarted near its own phase, or under a fast wah)
  can still read as one longer note.
- Tests: `tests/test_melody_split.py` reads held legato notes (plain and under a 6 Hz wah), eighths stopped dead,
  and same-pitch lines struck again: quarters with a gap, a decaying note with no gap, eighths stopped dead, and a
  flat note retriggered legato.

### song_compress: a finished song's WAVs as FLAC, bit for bit (ledger:M221)

- Stems are about 29 GB of WAV, and FLAC keeps every sample at about a fifth of the size. New op
  `song_compress(project, dry=True)` turns `renders/stems/`, the reference's stems folder and the named renders
  (`renders/*.wav` except latest.wav) into FLAC at the WAV's own bit depth, reads each one back and compares every
  sample, and only on an exact match moves the WAV to `songs/_reclaim/flac_<date>/<song>/` (a `manifest.jsonl`
  lists every move). It never deletes: the space comes back when the user empties `_reclaim` (hq:D-183).
- Dry by default: the report gives the files, GB now, GB after (estimated from three files), the skipped files and
  why, and the song's own scripts that name a `.wav` (found by grep; their owners' code). `dry=False` runs in a CPU
  slot on the shared machine, one song per call. Never touched, as code in the op: float WAVs (FLAC has no float),
  anything under `live/`, `sounds/` (the render cache keys on their mtime), takes and phone folders, exam folders,
  and `boom_trance/takes`, `vox/exam/clips`, `vox/work/cache`, `vox/rec`.
- Readers find `<name>.flac` when `<name>.wav` is gone (`ismail/audiofile.py`: `find`, `listing`): every analysis
  source (`render`, `ref:<stem>`, `track:<name>`, a path), the comparison stems, swing and grid on the drum stem,
  the sketch's tune balance, and `ref_retune`. A windowed render keeps its window as FLAC. Writers stay WAV: a new
  render writes `<name>.wav` beside the old FLAC and wins, and the next run moves the old FLAC to `_reclaim`.
- `render(out=...)` writes `renders/<out>.wav` once and makes `latest.wav` a hard link to it (one copy on disk; a
  drive without hard links gets a copy as before). `write_wav` never writes through a linked name: it writes a
  temporary file that replaces the name, so a later render never changes an earlier named one. An existing
  latest.wav that copies a named render becomes a link when the song is compressed.
- Listed in `guide`, `docs/reference.md`, the `ops_find` start shelf and the playbook's machine section.
- Tests: `tests/test_flac.py` (find and listing, each switched read site on FLAC-only stems, the latest.wav link
  across renders, the dry report, the exact read-back, a float WAV skipped, the moves and manifest, a newer WAV
  beside an old FLAC, the never-touched folders).

### analyze_melody: a quick octave jump reads in its own octave

- An eighth-note octave bounce (E2 E3 at 124 bpm) read its E3s as E2. pYIN's smoothing held the lower pitch through
  the jump: the lower note's period is also a period of the upper one, and the path may move only about two
  semitones per frame, so it stayed low for every frame of the upper note, and the pitch refinement searches only
  one semitone either side. Held (legato) notes read low every time, on a plain sine and on a harmonic tone alike.
- Each note's octave is now checked against its own spectrum: a note read at f whose odd partials (f, 3f, 5f) are
  20 dB or more under its even ones (2f, 4f, 6f) is at 2f, and moves up (never above fmax). A true low note keeps
  its octave, a missing fundamental included, since its 3f still sounds (ledger:M220).
- Tests: `tests/test_melody_octave.py` reads an A2 A2 A3 A2 eighth-note line on a sine and on a six-partial tone,
  and checks the octave check on a low saw, a saw without its fundamental, and a sine an octave up.

### analyze: pitch in cents per note, and a pitch contour (ledger:M225)

- `analyze_melody(cents=True)` gives every note's measured pitch as numbers, for scoring maqam steps (quarter
  tones) and intonation against a reference: tab-separated rows `start note dur vel midi hz cents`, where `midi` is
  the pitch as a float (note + cents/100). The reading already kept each note's offset but printed it only as a
  `# +NNc` comment past 25 cents. The default output is unchanged.
- New op `analyze_contour(source, bars, hop_ms=10, fmin, fmax, start=0, max_rows=200)`: f0 per frame with time,
  bar, beat, Hz, float MIDI, note and cents, and pYIN's voicing probability ('-' unvoiced), for slides, vibrato and
  ornaments. One window of at most `max_rows` frames (cap 1000) per call, the last line names the next `start`;
  only the window is analysed, so bars may span up to 64. pYIN gives voicing and the octave, YIN's interpolated
  pitch the cents where the two agree. Listed in `guide`, `docs/reference.md` and the listening reference.
- Tests: `tests/test_note_cents.py` (a tone 50 cents flat, a line with three-quarter-tone steps, the window cap).

### phone: one beatbox classifier (ledger:M222)

- `ismail.phone.beatbox` reads a beatbox voice note into drum hits: `read(y, sr, ref=None)` finds the hits (flux
  from 100 Hz to 10 kHz, the music under the take taken off when `ref` is given) and classes each one kick, snare
  or hat; `classify(hits)` is the rule alone, for hits found another way. The hits are ranked against each other
  on the take: two-means on the log spectral centroid splits dark (kick) from bright when they sit an octave
  apart, and among the bright ones two-means on onset strength splits the snares (hardest) from the hats. Each hit
  carries a `margin` (how far it sits from the cut that decided it), so a script picking its clearest hits does
  not need a band rule of its own. The take is scaled to a -3 dBFS peak first, so a quiet take keeps its quiet
  hits.
- Why: three copies of the rule in a live set's scripts drifted apart. The absolute one (more than 30% above 4 kHz
  a hat, more than 35% under 250 Hz a kick) read 5 of 50 kicks on a real earbud take, where the mouth kick is a
  dark thump with little under 250 Hz; the relative rule matches the kicks the person played there, and reads the
  same classes at any take level.
- Tests: `tests/test_beatbox.py` on a generic stand-in (dark thumps over 18 dB of level, hard bright bursts, quiet
  airy ticks, a speaker beat under the take). Docs: `jam-runner.md`, step 5.

### docs: the Live DJ's lessons in the live and phone references

- `references/live.md`, "Played keys": say a take fits only after reading the grid fit's numbers, and a recorded
  (audio) take gets the same fit on its unrounded onsets (`fit_grid`, the fit `live_played` uses).
- `references/live.md`, "A set's folder": read the section code and `setlog.md` before describing a past set, and
  serve a page of the set's files over http, never as a file (a file page's relative audio fails in a browser pane).
- `references/phone.md`, "Watching the phone" step 2: the first line of a passed-on note or an answer to another
  agent's ask says who it is for and what it is for.
- `references/phone.md`, `phone_exam`: a raw take sits about 15 dB under a bounce with its peaks near -3.5 dBFS, so
  the pre-flight's 1 LUFS check refuses the pair; play it on the computer's page or send a labelled limited copy.
  (ledger:M223)

### docs: a quick start the melody reader reads right

- The CLI quick start in `docs/reference.md` writes `0 E2 1 110; 1 E3 1; 2 G2 1; 3 B2 1` on `preset:acid_bass` at
  `volume_db=6`. The old line's sixteenth-note E3 (121 ms at 124 bpm, half of it inside acid_bass's 60 ms glide up
  from E2) read back as D#3 +42c with a second-part warning, and the render was flagged QUIET at -24.2 LUFS. The new
  one reads back note for note (voicing 0.50, no warning) and renders at -16.0 LUFS, peak -8.5 dBFS. A clean held E3
  on acid_bass reads right: the reader was not wrong, the example was too short for the glide (ledger:M214).
- Tests: `tests/test_quickstart.py` runs the quick start's own lines from the doc through the CLI and checks the
  notes read back and the level.

### phone: under each note, who took it

- The page writes one small line under each of the person's voice notes: "taken by X" (the agent it was said to,
  then the agent that claimed it with `phone_claim`; "taken by Voice, Director" for both), or "nobody has it yet".
  It comes with the page's state from the claims the server keeps, and a claim wakes the page so the line changes
  at once. The person's ask (ledger:M219, hq:D-179): two agents each skipped an untagged note, sure the other had
  it, and the phone could not show whether a note landed with anyone. The lines survive a restart (reading the
  inbox back after one no longer skips its first line when the whole file fits).
  Docs: `phone.md`, "Watching the phone" step 2. Test in `tests/test_phone.py`.

### Standing grants: the person's sources first, routes stop at what is always asked, a number on watcher_down

- An entry with a `source` now matches before one without, so an old unsourced grant with a wide keyword ("merge")
  no longer shadows a sourced one that names an owner. `grant_list` marks an unsourced entry and says how to give it
  a source (`grant_drop`, then `grant_add` with one).
- A route no longer carries an ask away from the person when it names something always asked (public, money,
  accounts, deleting) or something only their senses answer (an exam, a listen, a headset test): `grant_check`
  answers `ask:` and the phone asks them.
- The heartbeat's `watcher_down` alarm carries `down_since` (epoch seconds of the watcher's last poll), so a lead's
  alarm reads a number instead of the time in the text.

### sketch: feeling words in the first brief, the tune level held (M181 F2, M213)

- A first brief's feeling words now shape the sketch, not only a next round's: "happy and bouncy" came back at 72
  and 70 BPM, called "slow and bright", and was offered "faster and bouncier". A lift word sets the lowest tempo
  (happy 92, bouncy 106, upbeat or exciting 112 BPM), a calm one the highest (calm 76, dreamy 80, sad 84), unless a
  tempo word or a named BPM says otherwise ("a slow happy tune"); a genre's own range moves only as far as the word
  needs ("dark techno" stays at 120-126). A feeling with a mode (happy, sad, spooky) picks it when no key is named.
  This also holds for the three styles a vague brief gets and for `styles=`, which now also takes the brief's gentle
  words. The feeling words also cover their plain forms (spooky, moody, gloomy, peppy, playful, sunny ...).
- The everyday-word choices never offer a feeling the person already asked for, in the brief or any round since
  (no "faster and bouncier" after "bouncy", no "sunnier" after "happy").
- The tune balance holds the 4-6 LU the `sketch` docstring promises. A tune 5.5 LU under the parts rose 8 dB and
  stopped at +2.5 LU; now the tune rises at most 8 dB (and never past its -1 dB peak) and the other parts come down
  the rest of the way, which is clean, where raising the tune further only drives it into the limiter. The reply
  adds the level measured in the final mix ("in the final mix it sits +5.0 LU"), and says so when it is outside
  4-6 LU (a gap over 20 LU is a part gone wrong and is not chased); `sketch_ready.json` carries it as `tune_lu`.
- Tests: six more in `tests/test_sketch.py`.

### stage: capture setup at a song time, and a keyed screen for the comp

- `stage_capture` setup commands take `"at_song"`: the command waits for that frame and runs on film time just
  before it is drawn (one at or before t0 runs at frame 0; one at or after t1 is refused, as it would never run).
- `{"type": "screen_key", "color": "#rrggbb" | "off", "material": | "object":}` in a capture's setup draws a
  material (default "screen") flat and unlit in a key colour, with no tone mapping, fog or shadows, so a comp in post
  finds what covers the screen exactly; "off" restores it. Captures only, nothing is saved.

### What a fresh clone sees first (QA rehearsal 10-16: F3, F6, F7)

- `ismail --help` no longer says "Installed from PyPI": ismail is not on PyPI yet, so it names the git+ install.
- The MCP handshake reports ismail's version (0.3.0) instead of FastMCP's own (4.1.0 on a fresh install), and the
  dependency is `fastmcp>=3.1,<5`, the range that has been run (3.1 here, 4.1 on a fresh install).
- The guide's plain-words steps no longer forbid what the first sketch's SAY block says ("It's a first try ... What
  would you change? Or name a song you love", Nate 10-08), and they are numbered once, in order (3p to 9p; "6p" was
  there twice).
- `import ismail.guards` on its own works again: api_guards read guards' word sets while guards was half-loaded.

### live: headroom against the real deadline, cutting a section over, the room speaker off and on

- `live_status` has a `headroom:` line: the mixed audio still waiting when the device pulled, at its thinnest since
  the last status, out of the 500 ms kept ahead (`headroom: 430 ms of 500 ... clean`; THIN under 125 ms, DROPOUT at
  0), and the slowest block in ms against its 23 ms. The `mixer: N%` line keeps its start (guards parse it) and no
  longer calls a high % a dropout risk by itself: it is wall time per block and climbs with other programs' CPU while
  the sound stays clean. Measured with the device's own pulls, so it holds for a speaker and for `device='none'`.
- `live_queue(clips, section=, cancel_section=, cancel_at=)`: clips and stops carry a set section's name (also a
  clip's own `section` key), and `cancel_section` takes back what that section still has to do from where the
  batch's first clip lands (or `cancel_at`, or the next bar with no clips): its later clips are removed, its playing
  ones cut there, its stops after it dropped, and its effect moves sent with `live_fx(section=)` (or `section` in a
  move) dropped, a glide in progress holding where it is. The batch's own clips are kept; untagged clips are never
  touched. `live_status` lists the sections ahead. djkit: `Set.q(clips, section=, cut=)` tags the clips, `Set.cut`,
  `Set.moves(moves, section=)`; against an engine started before sections it queues untagged, as before.
- `live_device(device='none')` and back mid-set, without a restart (it existed since M34; the gaps are closed): any
  spelling of none, a reply and an `output off:` status line that say the room is silent while the clock, streams,
  recording and `live_listen` go on, a set started silent still follows the default and catches a stall once moved
  to a speaker, and a quick none, speaker, none no longer leaves two silent clocks pulling audio.
- The 489% mixer peak while rigging tracks into a running set: no single cause in the engine. Rigging nine tracks
  holds the engine lock about 1 ms each, chains build outside it, and an emulated rig on a quiet machine peaked at
  63%. The peak is the machine: each new track sends a warm-up render to every worker, and on a machine already at
  86-100% CPU one block took about 113 ms of wall time while the cushion covered it (no underruns); the headroom line
  now shows that. One stall that was the engine's is fixed: `live_track` resolved the instrument under the lock (a
  first mimic instrument imports its module, 39 ms; a `track:` spec reads project.json), and a bad instrument left a
  half-made track.
- Tests: `tests/test_live_headroom.py`, `tests/test_live_sections.py`, two more in `tests/test_live_device.py`.
  (ledger M128, M129, M130)

### machine: MISDECLARED, HUNG, ORPHAN, the pagefile's drive, and `machine disk` beyond songs/ (ledger:M122-M126)

- MISDECLARED (M122): a job's meter reads its process tree's share of the GPU's 3D engine every 5 s (Windows' own
  per-process counter through PDH; nvidia-smi gives none on a GeForce). A `--cpu` job whose tree averages 30 % or
  more over a minute is MISDECLARED on the board, told once in its own output, and counts against the GPU slot.
- HUNG (M124): a job past 2x its `--est` whose tree used under 2 % of the CPU and no GPU over the last 5 min is HUNG
  on the board and told once in its output; it is never stopped, and the flag leaves when it works again. With no
  per-process GPU reading the whole GPU's load decides, and with no reading at all there is no flag.
- ORPHAN (M123): a top CPU user whose parent process is gone (or whose parent pid was reused) shows as ORPHAN with
  how long it has run, on the board and in a refusal that names the top processes.
- The pagefile's drive (M126): the disk line shows the free space of a pagefile drive no job writes to (C: since
  10-05) and warns PAGEFILE DRIVE LOW under 10 GB free; a warning, not a gate. live_status shows it too.
- `machine disk` beyond songs/ (M125): the folders beside the checkout that holds songs/ (the top-level folders of
  D: here), or `$ISMAIL_DISK_ROOTS`, or `<board>/roots.txt`, each with its size and its growth since the last day's
  snapshot. A call walks for 20 s at most (`DISK_ROOTS_BUDGET_S`, `--budget N`, 0 for no limit), the cut walk
  first, then the cached biggest, then the rest; a root it did not reach says "not walked yet", one it cut says
  "at least", and the next call carries on from the folders left. Each root keeps its own last whole size and time
  in `latest.json` and is walked again after 30 min (`--fresh` now, `--songs-only` skips it). Growth shows only for a
  root walked whole both today and on the snapshot's day.
- Every new probe fails soft (no reading, no flag), and a watch that breaks says so once and leaves the job alone.
- Tests: `tests/test_machine_flags.py` (made-up samples and processes; no real GPU, orphans or drive walks).

### composition: a melody that travels through instruments

- A method in `references/composition.md` (ledger:S50, from the Live DJ's set): one 8-bar line handed to a new
  instrument at each section while the groove stays fixed, each pass fitted into the voice's measured range by one
  helper (whole octaves, a lead kept a few semitones under the measured top), each hand-off checked as a new row in
  `analyze_structure(source='render')`.

### exams: measure first, then spend the person's ear

- `phone_exam(..., measured=, unmeasured=)` (ledger:M212; qa intent_rules 10-16 cluster 5): an exam goes to the
  phone with the lines the instruments gave (`{'all' or a clip label or pair number: lines with their numbers}`) or
  the reason only the ear can tell (logged in the phone's `ear_first.jsonl`); with neither it is refused, saying
  which to pass. Either one is kept in `<answers_path>.measured.json` for the exam ledger.
- `exam_check` refuses a pair that is the same audio: aligned within 250 ms and level-matched, a difference under
  -40 dB is one sound twice (pairs by label, 1A with 1B; every two clips when the labels name no pairs). The line
  names the offset and the level, so a wrong file or a render that did not change shows at once.
- Docs: `blind-tests.md` "Measure first", `phone.md`. Tests in `tests/test_exam_check.py`; the phone tests pass
  `unmeasured`.

### phone: a watcher that stops while the person is live is named, once

- The watcher heartbeat (ledger:M211; qa intent_rules 10-16 cluster 3, "be reachable"). While the person is live
  (the Quest page open, a phone on the stream, or a `phone_expect` for a set, VR or a jam), an agent that watched
  in the last 30 min and has not polled for 2 min is down. One `alarm` line (`watcher_down`) goes to the lead's
  watcher (`phone_watch lead=true`), or to every watcher when no lead is up. The page shows "not hearing you: <who>",
  and `phone_status` and `phone_presence` name it; `watcher_back` follows when it polls again. A watcher skips an
  alarm addressed to someone else, and its own.
- `phone_expect(who, why, minutes=180)` and `phone_signoff(who, why)`: a watcher that must stay up, and a clean stop
  that raises nothing. `phone_presence` also says whether the person is live, who the lead is and who is expected.
  Docs: `phone.md`, "Watching the phone" step 6. Tests in `tests/test_phone.py` and `tests/test_phone_watch.py`.

### Standing grants know whose step it is: grant_check, grant_add, routes and the still-ask list

- Grants gain `owner` and `kind` (ledger:M209-M210, the 10-16 intent-rules review, clusters 1 and 2). A grant with
  an owner is that agent's: its own ask is refused with "act on it", anyone else's with "this is <owner>'s: send it
  to them". A route (`kind: "route"`) sends any question with its keyword to the owner instead of the person.
  Phone and stage asks pass `sender` to the check, and overrides log the verdict and the sender.
- A still-ask list no grant covers: anything public, money, accounts and passwords, deleting. A matching grant then
  only adds a note, and the question goes to the person.
- New ops on a new `asks` shelf: `grant_check(ask, sender)` (`act:` / `send:` / `ask:`, for a NEEDS NATE line or
  any ask no op sees), `grant_list()`, `grant_add(says, keywords, source, owner, kind)` (the person's own words as
  its source; keywords that match any ask and still-ask words refused; a broken file is never overwritten) and
  `grant_drop(id, why)` (kept in `grants_dropped.jsonl`). Tests: `tests/test_guards.py`.

### master_cue: a finished cue mastered for delivery, measured

- `master_cue(source, start_s, end_s, lufs=-16, tp=-1, sr=48000, out, fade_in_s, fade_out_s=0.4)` (ledger:M207):
  cuts the cue, fades its ends (cos^2), resamples, then applies one fixed gain to the loudness target (BS.1770
  integrated). Only when that gain puts the true peak (4x oversampled) over the ceiling does a lookahead peak limiter
  take the excess; its gain reduction is reported. It never rides the level (ffmpeg loudnorm falls back to a dynamic
  mode on some files). Writes 24-bit wav, by default `<source>_master.wav`; a file already there moves to `old/`
  first, and the source is never written. The report (LUFS, true peak, gain, limiting) is the cue's measurement line
  before anyone listens, with a warning when the target is too loud for the mix.
- Generalised from `songs/d3_next_teaser/work/master.py`: on the teaser's render it gives the same file, sample for
  sample (-16.0 LUFS, -1.1 dBTP, 0.2 dB of limiting). Shelved under `render` for `ops_find`. Tests:
  `tests/test_master_cue.py`.

### stage: cameras that follow, baked into keys for the render

- `stage_follow(scene, object, target, offset, space, lag, look, look_offset, look_lag, orbit, snap, where)`: a
  camera (or any object) soft-follows an object, a body's joint (`"<person>:<bone>"`) or the person (`"person"`).
  The offset is in the target's facing by default (`[right, forward, up]`), or Blender world with `space="world"`;
  a camera looks at its target with a level horizon; `orbit` circles it. The smoothing is time-exact at any frame
  rate (a step is capped at 1 s after a stall). No object lists the follows; `target=None` stops one; `where` reads a
  target. A follow waits while its body is not shown, and an error stops it with `behaviour_error` phase `follow`.
- Behaviours: `s.clock()`, `s.where(target)`, `s.place(name, o)`, `s.follow(target, o)`, and a per-frame hook
  `frame(s, dt)` (its first error stops it, phase `frame`). Reloading behaviours stops their follows.
- `stage_camera_record` gains `source="follow"`, the default on a following camera (otherwise `head` for a camera
  and `hold` for anything else, as before): the follow is snapped at t0, sampled while the clock plays and stopped
  at the end, so the keys play; Redo and Undo start it again, Keep saves the keys to anim.json for the Blender
  render. The camera menu's Record move picks it too.
- Tests: `tests/test_stage_follow.py`; a headless run on a scene copy checked an object follow (settle, aim, level,
  a 2 m jump at 69 % after one lag), a body's head, an orbit at 90.3 degrees a second, a bake of 19 keys within
  1.8 cm, Keep into anim.json, and a behaviour with `s.follow`, `frame` and a Stop menu.

### phone_presence and phone_claim: who is listening, and who passes a note on

- `phone_presence()`: both sides, many to many. The agents calling the phone server in the last 90 s, each marked
  watching (its `phone_watch` is waiting now) or not; the person's devices with the page open (phone, computer,
  tablet, Quest, from the browser, each page with its own id), with the browser, last seen and whether it is on the
  stream; the untagged notes nobody claimed in the last hour (LEAD past 3 minutes: the team lead's by default); and
  the recent claims. `phone_status` gives one line of it, and the page names the devices when more than one is open.
- `phone_claim(n, by)`: the agent passing on (or answering) note `n` says so first. The first caller wins, decided
  under the server's one lock, and gets "yours"; everyone after gets "taken: X claimed note n at HH:MM". Claims are
  kept in `~/.ismail/phone/claims.json`, survive a restart, and every `phone_listen` and `phone_watch` line of a
  claimed note carries `claimed {by, at}`. `phone_watch` returns `also_watching` and, for untagged notes, says to
  claim before passing on. The person's ask (10-16): untagged notes reached nobody, or six agents passed one note to
  the same lead, because every watcher guessed what the others would do.

### sound_zoom, sound_zoom_diff and region_measure: zoom into a sound, and find where it differs from the real takes

- `sound_zoom`: one time x frequency box of a sound at two resolutions, stacked in one PNG: time-sharp on top
  (256-point window, 32-sample hop at 44.1 kHz) and frequency-sharp below (2048-point). Both panels sit on
  `spectrogram`'s eye-mode plot box (the same picture code, now with a window and hop per panel), so a box has the
  same pixels in each, and the .json beside it maps a pixel to (s, Hz) and one address in either panel.
- `sound_zoom_diff(test, refs)`: where a take differs from 3 or more takes of the same words, beyond their own
  take-to-take spread, at three scales (grain, strokes, plumes), measured on power; with 4 or more, every take in the
  test's place makes an own-source control. Hot-spot text first (scale, band, quarter of the word, brighter or
  darker, z, the control rate in the same cell and whether the spot is beyond every control spot there; a spot
  counts only where the control lights its cell less often; capped at 12), then a picture of at most 1600 px: the
  test above the anchor take, the counted spots outlined. With 3 takes the spots are listed uncontrolled and none is
  counted. Every clip is set to -25 LUFS first, peak under 0.89 (vox's level match; `level_lufs=None` when you
  matched them). With fewer than 3 takes it refuses and says what to record; there is no absolute threshold. `debias=False` keeps a level across a whole band, which the de-bias
  absorbs.
- `region_measure`: power in a box of time and frequency, in dB, and its share of the whole spectrum at that moment.
- The method moved from the Voice agent's work (`ismail/zoom.py`: `spectrogram_diff` with its defaults unchanged,
  `region_measure`, `smooth_path` and `frac`, the synthetic parity fixture). On the fixture, seeds 0 to 9 with every
  take as the anchor, it gives the same maps and spots as the original, cell for cell.
- New tests (`tests/test_zoom_diff.py`, synthetic audio only): the fixture's box found at the strokes and plumes
  scales, +5.9 dB in `region_measure`, the one stray cell at seed 4, the refusals, 3 takes without a control, power
  against mean of dB, the de-bias and `debias=False`, the control-rate rule, the caps, the level match (with its
  peak cap), and the two-resolution picture.
  `ISMAIL_ZOOM_ALL_SEEDS=1` runs all ten seeds.

### stage: replay a take as input, ask the person for a test take, keep the last 2 minutes

- `stage_take_replay(scene, take, start=, end=, rate=, grab=)` plays a recorded take on a page nobody wears: the
  camera takes the take's head pose and each tracked hand becomes a hand on the page (25 joints with their radii),
  so the page's gestures, touch and grab read it through the same code as a live hand. The reply lists the page's
  events while it played, a "grabbed / landed onto" line, the take's own recorded marks and anything still held
  at the end (let go when the replay ends). `stage_take_replay_stop` ends one early. New page module
  `page/replay.js`, page command `replay_input`, events `replay_start` and `replay_done`.
- `stage_test_take(scene, ask, name, seconds=15)` asks the person in VR to act something out, records it as
  `test_<name>`, and labels it with the ask (meta `kind: test`), so an agent can test offline with the person's
  own hands later. A take recorded outside VR has no frames, and the reply says so.
- `stage_take_keep_recent(scene, seconds=30, name=)` keeps what the person just did ("keep that") though nothing
  was recording: while anyone is in VR the page holds their last 2 minutes of head, hands, body and grab marks in
  memory at 15 Hz (no audio), until a reload. `info=True` says how much it holds. Page command `take_keep_recent`,
  event `recent_kept`, meta `from: recent`.
- Limits: hands only (no controller takes, no recorded selects or sticks), the target ray is approximated from
  the head through the palm, grab hold and release counts use wall time (rate 1 for a grab verdict), and a replay
  is refused while someone is in VR on that page and stops if VR starts.
- New tests (`tests/test_stage_replay.py`).

### Fixed: spatial on speakers no longer moves a side sound to the other speaker as ctc rises

- With `out='speakers'`, `ctc` above 0 blended a pan (no interaural delay) with a binaural feed (with one) and
  ran a crosstalk canceller of the same strength on the mix. The two copies combed: at the default `ctc=0.5` a
  steady note at 70 degrees right came out louder on the LEFT (1 kHz: -8.8 dB with the default room), and the
  ears got less separation than plain panning.
- Now the speakers get one render, the plain equal-power pan with every distance cue, and `ctc` sets only how
  deep the crosstalk canceller runs on it (delayed and shaped by the head-shadow ratio of the far ear, above
  150 Hz, gain bounded at 0.9). With one render nothing can comb; `ctc=0` is plain panning sample for sample;
  the near speaker is the louder one at every frequency and every `ctc`, and the far ear hears less of a side
  sound as `ctc` rises (70 degrees, ears, 300 Hz to 3 kHz: +3.3 / +8.5 / +14.7 dB at ctc 0 / 0.5 / 1; before:
  +3.3 / +3.4 / +5.4). A sweep or a jump of `ctc` glides, with no click.
- On speakers the interaural delay and the pinna echoes are no longer in the feeds (they stay on headphones);
  the rear darkening stays. The speaker path costs about a third less CPU.
- New tests (`tests/test_spatial.py`): sines at 500 Hz, 1 kHz, 3 kHz and pink noise at 30, 70 and 90 degrees on
  both sides, at every `ctc` from 0 to 1, at the speakers and through a simulated speakers-to-ears path.

### spatial: move a sound in 3D, for stereo speakers or headphones, in the studio and live

- A new effect type, `spatial` (`ismail/spatial.py`, live twin `ismail/live/spatial_blocks.py`, one numba kernel
  for both). Position in metres around the listener (`x` right, `y` front, `z` up), automatable, or a `trajectory`:
  keyframes (linear or smooth, written as x/y/z or az/el/dist), an analytic orbit, a line or a flyby, with loop and
  start time; errors say how to write it. Studio trajectories run on song time; live ones start when set or inserted.
- The chain: mono sum, air absorption, `occlusion` (direct path only), one fractional delay line for Doppler and
  the interaural delay, clamped 1/r gain and a ground reflection (`ground` soft or hard), a spherical head model
  (head shadow, pinna echoes for elevation, a rear cue), then `out='headphones'` (binaural) or `out='speakers'`
  with `ctc` 0 to 1 (0 plain equal-power panning; above 0 a recursive crosstalk canceller for speakers at
  +-30 degrees, experimental). The effect carries its own early reflections and diffuse tail (`room`, `size`),
  which fall slower with distance than the direct sound. `spread`, `listener_yaw` (automatable), `doppler` 1 or 0.
- Optional measured head: `hrtf='sadie_d2'` (SADIE II KU100, Apache 2.0) after `samples_fetch('sadie_d2')` on the
  person's yes; converted once into `~/.ismail/hrtf` (reading the SOFA file needs h5py). Without it the built-in
  head is used and needs no data. `samples_list` shows it as a set the spatial effect plays. The download link and
  size are not yet checked against the York site: the first fetch, after the person's yes, confirms them.
- Live, a far source is not cut: its sound takes up to 1 s to arrive, so a track or bus holding `spatial` waits
  1.25 s of quiet (not the usual 0.5 s) before it goes dormant. Each live processor can now ask for such a hold
  (`Proc.hold_s`, summed per chain, next to `noise`).
- Fix: a change to `rig.py` (and now `spatial.py`) invalidates cached track renders; before, a rig change could
  serve a stale render.

### feedback_page: a page the person answers by voice or typing, kept on this computer

- `feedback_page(project, round, items, questions, voice=True, title=None, open=True, port=None)` writes a page
  into `<project>/work/feedback/<round>/`: the items (clips, pictures or text, each with a label), then numbered
  questions, each with a text box and a Record button. One shared server on 127.0.0.1 serves every round (found or
  started by the op, on a free port it records); the page opens in the person's default browser, since an app's
  browser pane has no microphone. Every typed answer is saved as it changes (`answers.json`, each write also in
  `answers_log.jsonl`), every recording as a file in `voice/`, transcribed in the background by the speech server
  the phone page uses, waiting while the CPU is busy. Reopening the page brings back the text, the recordings with
  their players and the transcripts; clips left waiting when the server stopped are picked up when it starts again.
- `feedback_read(project, round, since=None)`: the answers verbatim per question, typed text and each clip's file,
  length and transcript, with times, bounded; says when nothing has come in yet and when transcription is still
  running. `feedback_list(project=None)` lists rounds, links and what is waiting; `feedback_stop()` stops the server.
- The skill points to it first for any desk page that takes the person's voice (`blind-tests.md` page mechanics,
  `user-experience.md`, the SKILL table); hand-built servers only for what it cannot do.

### skill: pages that take the person's voice open in their own browser

- `blind-tests.md` (page mechanics) and `user-experience.md`: a desk page with voice boxes, recorded takes or any mic
  input is served locally and opened in the person's default browser, since an app's browser pane has no microphone,
  and it brings back everything they typed or said when reopened. Agents kept rebuilding this; the rule points at the
  pages that already do it.

### stage_preflight: one check before every VR session or demo

- `stage_preflight(port=, say=, wait=, headless=)` runs on its own scratch scene, `_preflight` (a floor and a
  box built in code, hidden from the page's scene list, overwritten on every run), and reports one line per
  check: server, presence, allow_rule, guard_hook, scratch, load (a headless, muted browser opens the scene and
  its selftest passes), hot_swap (`stage_asset_install` of a new `scene.glb`, answered by the page's reload),
  live_insert (`stage_world update=`, then `stage_set_load` unload and load), say (`stage_say`, a caption) and
  listen (a synthetic `preflight_ping`, marked synthetic, comes back through `stage_listen`). Each is PASS,
  BLOCKED with the next step, or SKIPPED, with its time; then the verdict, shown on the stage (in VR the headset
  says it; never spoken on the computer).
- The settings checks only read `~/.claude/settings.json` and the project's `.claude/settings.json` and
  `settings.local.json`. A missing allow rule or guard hook is BLOCKED with the exact entry to add and where.
- While someone is in VR, the checks that need a test page are skipped and say so; the person's own scene is
  never loaded over, moved or reloaded.
- `stage_listen` now also hears `preflight_ping` (a server started before this change answers BLOCKED: restart
  it when nobody is in VR). `stage_asset_install` takes the scratch scene `_preflight`.

### stage: a command sent right after a scene switch runs

- `stage_scene_go` now answers only after the page has read the new scene's last command id, so an op sent the
  moment it returns is newer and runs. Before, the page's first poll on the new scene took every queued command as
  old, and an op sent within about 2 s of the switch was dropped (it timed out after 30 s). A poll that was still
  reading the old scene when the switch happened is ignored.

### toms: real toms retuned to the note, struck where and how you say

- `toms` (drums, performer): the Big Rusty toms (Big Rusty Drums by Karoryfer Samples, CC0 1.0: 14", 15", 18"
  and a 22" kick played as a floor tom). The note is the drum's pitch: the nearest real drum is retuned to it, each
  recorded layer from its own measured pitch, its ring bent back to a drum at that pitch, then checked as rendered
  and corrected toward the real drums (pitch per round robin, ring fitted to their envelope). Lanes `pos` (0
  center .. 1 rim: the fundamental gives up 6 dB toward the rim, fitted on the snare's center and edge hits) and
  `stroke` (stick, mallet, brush, rimshot, rimclick, shell, dead) per hit; velocity picks the recorded layer.
  Heard as real F#1-A3; in a blind exam of 5 pairs one listener found the real drum in none. About 2 s per hit.
- Samples: `$TOM_SAMPLES`, then the project's `ref/` (any Big Rusty Samples folder holding tom_14, tom_15,
  tom_18 and tom_22), then every folder of the `big_rusty` set (`samples.paths`: a registered copy may lack the
  toms). Missing, it says to `samples_fetch('big_rusty')`, which already brings every tom folder and stroke.
  The closed loop's corrections are kept in `~/.ismail/cache/toms` (`$ISMAIL_TOMS_CACHE`), one file per
  version of the voice's code and data.
- Performers that name `project_root` get the project folder (studio and live).
- `sketch` reaches for it on toms, tom fills or grooves, floor, rack, tribal, melodic or pitched toms: a `toms`
  part on the key's tonic, a fifth and an octave; a tom groove alone, only the fills over a kit. With no toms on
  the machine the part is left out and said (nothing stands in for a pitched tom).

### Camera moves in VR: the path on the clock, and a move recorded like a take

- `stage_trail(object)` (page command `trail`) draws a keyed camera's path over the clock span in the room: a line
  coloured from the span's start to its end, a small frustum and the time at each key, and the playhead bright. In
  VR a key picked on the ray scrubs the clock to it; carried, it moves the key (same time, new place). The camera
  menu has Path / Hide path.
- `stage_camera_record(camera, action, source, t0, t1, countdown)` (page command `cam_record`, menu: Record move):
  after a countdown the clock plays from t0 to t1 while the camera is the person's eyes (`head`) or wherever it is
  carried (`hold`). The pose is sampled every frame, smoothed (a local quadratic fit, so frame jitter and hand
  shake do not become keys) and thinned to smooth keys within 2 cm and 2 degrees; they replace the camera's keys
  inside the recorded window and keep the rest. Keep saves anim.json; Redo and Undo put the old keys back.
- `interp.js` `thinKeys` and `easeSamples` (shared by recorded moves); the clock holds a recorded object off its
  keys while it records.

### Grab by touch: hand-sized things, a let-go after an open-handed take, a steadier count

- A hand takes the outermost group it could carry (up to 2.2 m across), not the outermost group: reaching for a pool
  ball took the whole pool table. `stage_grab_rule(grab=True)` still allows a big thing.
- The grip locked at the take counts as at least half closed (120 degrees of bend), so a thing taken with an open
  palm lets go when the hand stays open flat; before, an open-handed take could never let go.
- The 1.5 s count pauses through a touch or stillness lost for under 0.4 s instead of starting over: one bottle's
  count restarted 12 times in 9 s.
- CLI: `key=False`, `key=True`, `key=None` are Python values, not strings (`grab=False` read as a truthy string and
  allowed the thing instead).

### Ops refuse: personal details on a page, questions already granted, a mic without the person's note

- One module, `ismail/guards.py`, holds the three checks; each refusal says why and what the next call is.
- Nothing personal on a page: every op that sends text to the phone page (through the one phone call path:
  captions, panels and their buttons and inputs, asks, exams, now-playing, buttons, offers) and every stage page
  command that shows text (say, ask, panel, cue, marker, and `stage_busy`) is scanned against a local list:
  `$ISMAIL_PRIVATE_TERMS`, else `~/.ismail/private_terms.txt` (one term per line, `#` comments), plus the git
  `user.email`. Whole words, any case; an email anywhere. A hit refuses with "Rephrase without it". With no file,
  only the email is checked and the reply says how to turn the scan on.
- Don't ask for what is granted: `phone_panel_show` (yes/no or approve/deny panels, told by their buttons),
  `phone_ask`, a `phone_say` that asks, `stage_ask` and yes/no `stage_panel_show` are refused when a standing grant's
  keyword is in the title or text. Grants: `$ISMAIL_STANDING_GRANTS`, else `~/.ismail/standing_grants.json`, a list
  of `{id, date, topic, keywords, says}`. `override_grant='<why it does not fit>'` lets the question through and
  appends the reason to `grant_overrides.jsonl` beside the grants file. No file, no check.
- The mic needs the person's own yes: `listen_start(mic_ok=...)` (and the recorder, and `session_rec.py
  --mic-note`) takes the id of their voice note or typed answer from the phone inbox, a VR voice note as
  `vr:<scene>:<event id>`, or `'chat: <their exact words>'`; a note sent in the last 12 hours, every yes naming the
  mic or recording and not a no. `mic_ok=True` is refused. The recorder checks the yes again before it opens a
  device.
- A chat yes is weaker, so each one is logged locally (`ts`, `op`, the quote's first 300 characters) in
  `$ISMAIL_MIC_CONSENT_LOG`, else `~/.ismail/mic_consent.jsonl`; the recorder checks it by its log line
  (`chat-log:<n>`), so the quote is written nowhere else.
- A typed answer counts only its free-text fields: the phone server now records an answer's `typed` field ids, so
  an option picked or a box ticked never passes for their words. Answers taken before the phone server restarts
  carry no `typed` and are refused as a mic yes (ask again).

### Worktrees: sweep the empty folders removed copies leave

- `python -m ismail.worktrees sweep [--dry-run] [--json]`: removes the empty folders next to the main checkout
  (`<main>-*`) and in `<main>/.claude/worktrees/` that git no longer lists as copies, with `os.rmdir` only, so a
  folder holding anything stays. A non-empty `<main>-wt-*` folder git does not list is named as "not a dev copy,
  left alone"; other projects' folders are not reported. A folder a program still sits in is reported HELD for the
  next sweep. It also drops the owner records of copies that are gone, as `list` does.
- `done`: on Windows git removes a copy's files and record but not its folder while a program's working folder is
  inside it (and exits 1). `done` now goes on, removes the empty folder itself, or says it is HELD instead of
  reporting plain success.
- The intake (`python -m ismail.handoffs`) counts empty leftover folders and names `sweep`.
- `worktrees.md`: the sweep, and the rule: never leave a shell's working folder inside a copy (`git -C <copy>` or a
  subshell).

### Stage dev: restarts check presence, not the person's word

- `stage-dev.md`, Deploying: a stage server restart needs only `stage_presence` saying not in VR and nothing in
  flight. The dev does not ask the person, and an open desktop page posting state does not count as them being in.

### Stage: named ops for every live edit

- `stage_asset_install(scene, file, kind, name=, scenes=)`: puts a room (`scene.glb`), a piece list
  (`manifest.json`), an actor body (`actors/<name>.glb`, in the scene the bodies come from) or a tree
  (`trees/tree_<k>`) into a live scene. The file it replaces is kept in the scene's `history/`; an open page reloads
  the room or the trees, and a replaced body shows at the next page load. It refuses a missing file, a wrong type, an
  unknown kind and an unknown scene with the next step.
- `stage_world(scene, update={...})` changes only the keys given (a key set to null goes back to its default);
  `world=` still replaces the whole file, and giving both is an error.
- `stage_restart(port=, scenes=, now=False, who=)` restarts a stage server with the arguments it was started with
  (servers now record them), waits for `/health` and replies with the port and scenes. While a page is in VR it
  refuses, says so in the headset, and names the way on: wait for `vr_exit`, or `stage_ask` and `now=True` on a yes.
- `skills/ismail/hooks/named_ops_guard.py`: an optional Claude Code hook that refuses `python -c` with ismail code,
  copies into a scenes folder, killing the stage or a live engine by process, and hand edits of `world.json`,
  `scene.glb` or `manifest.json`, naming the op to use instead (`skills/ismail/hooks/README.md`).
- The hook fails open: the settings text in its README checks the file exists before running it (a hook exiting 2
  blocks the call, and Python exits 2 on a missing script), and any error inside the guard lets the call through.
- `references/stage.md` has the table of blocked forms and their ops.

### Live: keys on the screen

- `live_keys_page(project, phone=False, sender=None)`: optional, for someone with no MIDI instrument who wants to
  play a part (a real keyboard needs none of it). An on-screen keyboard that plays straight into the running engine
  as the input `'screen keys'`, no loopMIDI: `live_midi(source='screen keys', ...)` patches it and `live_played`
  reads it back (`live/midi/screen_keys.jsonl`).
- Served by the engine on this computer (`http://127.0.0.1:<port>/keys`) and by the phone server (`/keys`, relayed
  to the engine); `phone=True` posts a phone panel that opens it. One small POST per key, no polling.
- Two to three octaves (two rows on a phone held upright), octave shift, a sustain toggle, velocity from where the
  key is pressed (near the top is soft), multi-touch chords, the mouse, and the computer keyboard's Z and Q rows.
  The top line says whether it is connected and what it plays. Each message carries a sequence number, so a
  late one never undoes a newer key.

### Stage: the gesture map

- What each hand shape means in VR is now data, not code in six page files: `stage_gestures(scene)` lists it (intent,
  shape, hand, hold, where it is read, whose row), `stage_gesture_set(scene, intent, shape=, hand=, hold_ms=, off=,
  owner="person" | "scene")` rebinds one, `stage_gesture_reset` goes back. The page reads `/gestures?scene=` and
  takes a change at once (page command `gestures_reload`). Nothing changes in feel: the defaults are the gestures
  and holds the page had.
- Who wins: locked and fixed rows, then the person's own map (`~/.ismail/stage/person/gestures.json`, every scene),
  then the scene's rows (world.json `gestures`, checked like the rest of world.json), then the engine.
- Locked: leaving VR (both thumbs up), the performance stop (both thumbs down), the voice note (phone). Fixed to how
  the shape moves: travel, the snapshot frame, select, hold. Rebindable: the update reload, the yes and no answers,
  drop. A rebind that would share a shape with another row where both are read is refused, naming that row and
  its owner. The update card and the answer prompt sit over the hand the map names and say its gesture.
- The yes and no answers can be rebound but never switched off: `stage_ask` is answered with them. Saved rows are
  checked when read too, so a hand-edited value that cannot be used (a hold that is not a number, a shape that does
  not exist, a row for a locked intent) is ignored with an `IGNORED` line in `stage_gestures` and the engine's value
  stands; the next save drops it from the file.

### Stage: edits apply in place, no reload

- `stage_material_set(scene, color, material= | object=)` sets a material's base colour on the open page now, as an
  undoable edit that saves to edits.json like a hand edit.
- `stage_world` with `world=` tells an open page (page command `world_reload`): it reads world.json again and redoes
  what was built from it (load sets, changed bodies, props, mouth tracks, keep-out boxes). Before, it waited for the
  next load or scene switch. In a `stage_batch` the reload goes with the page commands after it, in order (one
  more reply line, `[i] stage_world (page)`).
- Pieces the Quest diet merged into their group keep their names: `merge_groups` numbers each piece's vertices
  (`_part`) and the manifest lists them (`merged`). The first op that names one (`stage_object_set`, `drop`,
  `select`, ...) splits it back out with its children; saved edits of a piece split it at load. Rooms exported before
  this split nothing until their next export.
- The stage docs: live edits go through named ops only (`python -m ismail <op>`, `stage_batch @file.json`), never an
  inline script; a missing op is the gap to fix.

### Live: played keys on the beat the player heard

- The key log's `beat` is now where the key sounded against the music (the press plus the output buffer, about
  180 ms on laptop speakers), which is where a player by ear meant it. It was the press, so a take read about one
  key delay early. The press stays in the log as `press`; old logs still read, and `live_played` says which clock
  it used and the delay it added.
- `live_played(fit=True)` fits a 16th grid with a free offset to the hits (keys within 0.15 beat are one hit),
  reports the offset, the rms error in ms, the drift per bar and the rhythm's shape (3-3-2), and prints the
  snapped notes for `live_queue` twice: as played, and with the shape's groups from beat 1. `bars=[a, b]` picks
  the bars, `from_bar` sets the clip's bar 1. It is the method that lined up a played riff in the 2026-10-13 jam
  after four tries at rounding.
- When every key in the bars has one velocity, `live_played` says so and suggests an accent shape (the data is
  unchanged).

### Live: played keys

- `midi_sources()` lists the MIDI inputs (keyboards, pads, loopMIDI ports). Windows reads them through winmm with
  no extra library; elsewhere mido with python-rtmidi when installed.
- `live_midi(project, source, track= or instrument=, volume_db, transpose, channel, remove)` plays an input through
  the running set: the notes render once on the workers, then a key sounds one output buffer after the press
  (velocity, note-off, sustain pedal, all-notes-off). Swapping the sound keeps the old one until the new is ready. A
  running engine says when an input is plugged in or out and reopens a patched one when it comes back.
- Every key is logged with the bar heard (`live/midi/<input>.jsonl`); `live_played(project, source, bars)` reads it
  back as notes lines, the format tracks take.
- Known gaps: keys skip the track's live effects and the safety limiter (a soft clip guards them), and are not in
  `live_record` recordings or streams yet.

### Listening together: words as they are said, sessions off the system drive

- `listen_heard(since)` gives the person's words a few seconds after they say them, cut at pauses or every 8 s
  at most, each with its time and the bar the live set had sounding (a new `clock` op on the live engine, exempt
  from its news). It uses the speech server the reading uses and starts nothing; when that server is down it says
  so and what still works. A chunk that fails is tried once more, then reported, never dropped. `listen_peek`
  points at it while the mic is on.
- Sessions go to `<project>/listen/` (`listen_start(project=)`), else the running live set's folder, else
  `$ISMAIL_LISTEN_HOME`, else `_listen/` in the installed songs folder: never the system drive by default. Ids
  resolve across all of them and the old home; a full path works too.
- A 2 GB free-space floor: `listen_start` refuses under it and says what to do, and a running session stops by
  itself there, keeping its recording, with a line in the next listen reply. Background temp files stay in the
  session folder.

### Listening together

- New `listen_*` ops record what plays on this computer while the person listens and talks about it:
  `listen_start` (speakers or another output, and a mic only with `mic_ok=True` after their yes; the reply names
  every live input, and the phone page pins the mic state), `listen_peek`, `listen_mark`, `listen_note` (a typed
  message, placed over the time spent typing it), `listen_stop`, `listen_read` and `listen_list`.
- The reading puts everything on one clock: skips, changes and pauses in the music, each stretch's loudness (LUFS,
  LRA, true peak), beat grid and overview, and what was said, placed at the moment of the music heard (the mic's
  lag and drift are measured). Recording and reading run in the background.
- Windows only for now: `pip install ismail[listen]` adds pyaudiowpatch. Built from the Live DJ's session recorder.
  See `skills/ismail/references/listen-together.md`.

### README: a short front page

- The README is now short: what ismail is (recursively modifiable interfaces: the DAW, the VR stage and the phone
  page), songs to listen to, the agent roles, install, where to look, where it falls short, credit and licence.
- The long reference moved to `docs/reference.md` (what's built today, the agent team, sample sources and every
  tool area), and `docs/why.md` says why it works this way.

### Render: faders in the readout, an empty artist tag, wait in minutes
- render's per-track readout says it is post-fader and prints each track's and bus's fader (and "(automated)" when
  the fader moves). A track routed to a bus is read before the bus's fader. When the master limiter is working, the
  readout says so: lowering one fader also eases the limiter, so that part's peak drops by less than the fader moved,
  and a part over 0 dB there is not a clip.
- `project_set(artist=...)` sets the artist tag render writes into its mp3s. `artist=''` writes none (and removes one
  already there), for a copy whose artist is added by hand later. Unset, it stays "ismail".
- render's and sketch's help say a bare `wait` number is minutes: `wait='10'` is ten minutes, `wait='600s'` is
  seconds.

### Stage: worn parts come off

- `stage_actor_part(scene, person, part, show, move, seconds, reset)` hides a piece of a person's costume (glasses,
  a mask) or slides it off over a few seconds and then hides it, riding the bone it is worn on so it moves with the
  head. Kept while the page is open, not saved with the scene; `reset=True` puts it back. Event `actor_part`.

### Stage: grab by touch

- In VR with hand tracking, resting a hand on a thing (the palm or the finger bases on it, the hand nearly still)
  for 1.5 s takes it; a ring at the palm fills while it counts. Opening the hand past half its grip for 1 s lets go
  (the ring empties; closing again keeps it), and the thing falls onto whatever is under it with the hand's speed, so
  a toss flies. Fingertips alone are still the pinch for menus, and the fist grab steps aside for things grab takes.
- Inside a person, their hand holds what you take, and the grab and the drop go into the take and play back with it.
- `stage_grab_mode(scene, on, objects, hold_s, release_s)` and `stage_grab_rule(scene, object, grab, fixed)`, saved in
  world.json `grab`; a fixed thing (a bar) keeps the hand where it took hold. Events `grab_counting`, `grabbed`,
  `grab_releasing`, `grab_released`, `grab_landed`. Grabbed things are never saved to the edits file.

### Stage: stepping into a person

- A third way to Follow: at GO the view moves inside the person, eyes at their eyes and facing their way, so their
  body is exactly where yours is and their hands are yours. Their head, your hand models and every panel, beam and
  label are hidden from the inside, so a headset recording is clean. Both thumbs down held (or "stop the
  performance", or an agent) stops it; you are put back where you stood and the take plays on them for keep or redo.
- The person menu has "Step in" (with a take); agents use `stage_embody(person, when=next|now|off, eyes=person|floor,
  after=return|stay)`. Events `embody_armed`, `embody_start`, `embody_stop`, `embody_skipped`.

### Stage: lip sync hears the voice with Rhubarb Lip Sync

- When Rhubarb Lip Sync is on the machine (`~/.ismail/tools/rhubarb/`, or `ISMAIL_RHUBARB`), `stage_face` and
  `face.track` take the mouth shapes from it: it listens to the voice itself, so the mouth follows the sounds and
  the pauses instead of splitting each word's time evenly. Its phonetic recognizer is the default (any language,
  singing too, 8 s of speech in about 2 s); `engine='rhubarb_english'` uses its English recognizer guided by the
  words (8 s in about 17 s); `engine='words'` keeps the word method. Rhubarb gives the same nine mouth shapes, so
  tracks and the page are unchanged.
- Without it, or when it fails, the words are used as before, and the reply says why Rhubarb did not run.

### README: a "please credit" section

- The licence stays MIT. The README now asks, as a request and not a licence term, that published work say "Made
  with ismail" with a link, points to the `credits` op and to GitHub's "Cite this repository" (CITATION.cff).

### Stage: `ismail.stage.link` imports on its own again

- `python -m ismail.stage.ear --who <name> --once` died with "cannot import name 'page_cmd' from partially
  initialized module 'ismail.stage.link'": link imported `ismail.api`, which imports the stage ops, which import link.
  Link now imports api only when it raises an error.
- `tests/test_stage_imports.py` imports every `ismail.stage` module first, in a fresh interpreter, so a cycle like
  this fails the tests (it passed before because the test process had already imported api).

### Stage: "heard by" receipts, busy lines, a stuck warning, and an ear that keeps listening

- A session sat ten minutes on a render while four voice notes went unanswered, and the person left the headset
  (2026-10-11). Now the moment a listener is handed a note, the headset card shows "heard by <who>" for a few
  seconds; `/live/version` and `stage_presence` carry the newest note's receipt (`receipt`: who took it, answered,
  stuck).
- `stage_busy(who, text, minutes)` (POST `/live/busy`) sets a session's one-line status ("rendering the drums, in
  line, about 5 min"), shown in the headset beside the receipt; `text=""` clears it, and it runs out after `minutes`
  (default 10, up to 60).
- A note no agent answered within 2 minutes (a say, ask or panel; the automatic "got it" does not count), taken by
  sessions with no busy line, gets "<who> has not answered for 2 minutes. It may be stuck." out loud, a
  `session_stuck` event (also in the inbox) and the `session_stuck` hooks (`STAGE_WHO` names them). A session that
  only listens is not called stuck when another answered.
- `python -m ismail.stage.ear --who "<name>"` listens to every scene until stopped: it prints one line per voice note
  or error, logs the rest to `~/.ismail/ears/<name>/ear.log`, resumes from its mark when started again within 15
  minutes, and follows a server restart (the inbox now says when its server started). `--once` exits after the
  first printed line.

### Stage: picking one thing straight from another's open menu opens its menu

- In VR, picking a person or object while another thing's menu was up closed that menu but showed nothing: the
  old menu's loop ended a moment later, so the new pick found it still busy and was dropped, and picking the same
  thing again did nothing since it was already selected. The last pick made while a menu closes now opens its
  menu once the old one has gone, if it is still selected, and picking the selected thing with no menu up shows
  its menu again (`page/menugate.js`).

### Skill: a jam runner recipe

- references/jam-runner.md: how to build a small runner in a song's folder that reads each phone take in code in
  about a second (classify, sync to the engine's beats, read into a part), the sample / match / ask modes on a phone
  panel, queueing on the loop, and turning the jam into a studio song. No new op: the runner is song code, built per
  jam from the recipe. live.md's "In a jam" and SKILL.md's reference list point to it.

### Skill: three paces, and a jam is threaded

- live.md, "Three paces: studio, set, jam": studio work and exams are turn-based, a set runs in real time
  underneath, and a jam is threaded: the main thread answers everything the person sends within about a minute and
  hands anything longer than a couple of minutes to a helper with full context, saying which loop it lands in.
  A new "In a jam" list, from the Live DJ's jam with the person: watch first, record from the first take, their own
  sound before a transcription, ask once whether a take is a sample or a melody, pull a wrong part at the next bar
  and fix it in a helper, keep developing when they go quiet, and write the story as you go. It was only in one
  agent's notes before (the person, 10-12: "you're the DAW").

### Skill: a piece copied from another song is a whole song

- SKILL.md, next to "One piece, one folder": a piece started from another song gets a complete song folder (what
  it takes to rebuild it, its provenance, its notes, and any renders brought along, renamed for the new piece), and
  nothing in it reads the old folder, so any agent can pick it up. Check the copy by rebuilding its project and
  comparing it with the source's before rendering (the person, 10-12: "any other agent could pick it up just like
  any other song").

### Phone: the background is still when no set plays, and phone_now works past 200 marks

- The page's background (its effect, the beat pulse, note reactions, hue drift) now holds still, in the look's
  colours, whenever no music plays: the set paused or stopped (both end the engine), or an engine left running
  with every track silent. It moves again, where it left off, when the music does (the person, 10-12: "more
  of like an indicator that things are playing"). The page's state carries `engine.sounding`, and a change
  wakes the page at once.
- `phone_now` failed with "'str' object is not callable" once more than 200 marks were saved: its `next`
  argument hid the builtin `next()` used to trim them, and the view had already changed. Marks are now
  capped at 200 without it.

### Tests: one phone test's voice notes no longer reach the next

- An old test's phone kept retrying its voice note through the next test's speech server and into its inbox; each
  test now stops its phones (`Phone.stop()`), and a note said while another waits to retry goes ahead of it.

### Stage: people's mouths move with a voice (lip sync)

- `stage_face(scene, person, at, audio | take, start, end, words, transcribe)` turns a stretch of a voice recording
  into a mouth track and plays it on the person at that song second, on whatever body they show (take, Follow, rest
  pose) and in captures, which move the music clock frame by frame. Words become phonemes with the CMU Pronouncing
  Dictionary when its data file is on the machine (else from the spelling), phonemes one of nine mouth shapes, and
  each shape a mix of ARKit face units (`ismail/stage/face.py`); loudness opens the jaw on loud vowels, the mouth
  rests between words and leads the sound by 40 ms. Tracks are saved in the scene's `faces/` and listed in
  world.json `faces`. The page (`faces.js`) sets the weights on the body's morph targets by name; a body without
  mouth shapes is reported once (`face_unsupported`). Bodies need mouth shapes, from mhx's export with
  `morphs="mouth"`.

### Stage: nine small bugs from the engine review

Each was wrong before this change (10-12):

- **The last-moment save** when the tab hides or closes now goes to the scene showing, not the one the page opened
  on. It sends the same request as a normal save (with `reverted`) and counts as saved only when the server answers.
- **A turn reported in an edit** (`delta.rot_xyz_deg` in transform events) read a Blender-space turn in three's XYZ
  order (Rx Ry Rz); Blender's XYZ is Rz Ry Rx. A turn on one axis was right; a turn on two or more was not.
- **Foot pins** (`stage_follow_anchor` joint `foot_l`, `foot_r`, `feet`) now hold on a standing person and over a
  start pose: the pinned foot goes to its pin and stays, and the other foot steps as before. Before, only a seated
  person read them.
- **`stage_view_look_through`** now looks through the camera. The op sent `camera` and the page read only `name`,
  so it always went back to the editor view. An object that is not a camera is now an error.
- **`stage_listen`** now hears `behaviour_error`, `object_message` and `page_error`; only `stage_events` showed them.
- **A page answer that carries an `error`** (no mic, already asking, a behaviour function that threw) now fails the
  op, and fails its step in `stage_batch`. Before, the op said done with the error inside the text.
- **Saving edits.json** copies the previous file to `history/` and replaces the file in one step, one save at a
  time. Before, it moved the file away first (a read in between saw no edits) and two saves at once raced on one
  tmp name.
- **Timeline playback** no longer leaks into saves: a keyed object keeps the place it was edited to, and the frame
  the timeline is on is not written into edits.json.
- **During a scene switch**, events go to the scene they happened in, voice notes to the scene showing when they are
  sent, and cue and waypoint saves to the scene they were made in.

### Skill: one piece, one folder

- SKILL.md now says when a new song folder starts: from the first file of a new set, a new jam, a studio track
  made from a jam or a remix. What a piece reuses from an older one is copied or imported by path and named in its
  notes; a piece never lives inside another piece's folder. The skill only showed what a folder holds, so every
  new piece went into the folder where the live engine happened to run.
- live.md, "A new piece while the engine runs": an engine belongs to the folder it started in, so moving to a new
  set is finish or fade, `live_stop`, `live_start` in the new folder, then the phone watcher on the new inbox. A
  finished song from another folder can still play on a deck by its path.

### Phone: one standing watcher for every agent

- New `phone_watch(who)`: the phone watcher every agent runs in the background (`python -m ismail phone_watch
  "who=<role>"`). It loops `phone_listen`, returns every line the person sent (a burst gathered with one more short
  look) and a `next` line saying how to re-arm. Nothing is filtered by owner; only page telemetry (every page
  action but `panel_close`) and the `voice` transcribing line stay out. Its cursor is kept on disk for each `who`
  and saved only when it returns lines, so a context reset or a killed watcher resumes where the last return left
  off, and with no cursor it starts at the end of the inbox instead of replaying it. It returns `alive: true` after
  `wait_min` (100 minutes) with nothing, waits out a phone server restart, and `round=<card id prefix>` holds a
  round of someone else's cards. Before this the watcher lived in one song folder; after a context reset a
  hand-made one kept no cursor and missed six of the person's notes in 36 minutes.
- skills `phone.md` names `phone_watch` as the standing watcher (re-arm it first after every wake, never write your
  own); `live.md` adds to the DJ loop: start it in the same turn as any set, and never put housekeeping before the
  phone.

### Live: a track's output, a set's loudness and the engine's priority, in the ops

- `live_track(output='<bus>')` makes a new track play into that bus instead of straight into the master, so the
  bus's stream carries only the tracks routed to it. The engine took it already; the op did not pass it, so the
  stream-per-object recipe in live.md failed with "bad arguments". A track's output is set when it is created;
  asking to move it now says so instead of being ignored (hq:D-131).
- `live_start(trim_db=, cap_db=)` sets the set's loudness per set: `trim_db=4, cap_db=-12` is about -16 LUFS. The
  arguments win over `ISMAIL_LIVE_TRIM_DB` and `ISMAIL_LIVE_CAP_DB`, which stay as the fallback. A tool server could
  not change its own environment between calls, so a set it started stayed quiet. The trim takes -30 to +12 dB and
  the cap -40 to -8 dBFS; the ceiling still comes only from the environment. djkit's `Set.start` passes them as
  arguments instead of writing the environment.
- `live_start` sets the engine's priority in code: above normal by default, with `priority='high'` or `'normal'`
  to change it. The engine sets it after its render workers start, so they stay normal. Where raising it needs
  rights the user lacks (outside Windows), the engine stays as it was and the reply says so. live.md had told agents
  to set it by hand, once as AboveNormal and once as High; both lines are now one line about the option.
- mastering.md and live.md say how a live set reaches its level: the arguments, then faders.

### Dev: dev copies (git worktrees) tracked by a tool

- New `python -m ismail.worktrees`: every dev copy of the repository (a git worktree) on one line, with its branch,
  owner, commits ahead and behind `origin/main`, merged or not, its last commit's age, its uncommitted and untracked
  files, and its open pull request (when `gh` is there). Flags: UNASSIGNED, MERGED (remove it), IDLE (unmerged, no
  commit for 3 days), BEHIND. The first line names the next action, and the next says when the main checkout is
  behind `origin/main`. `--fetch`, `--json`, `--unassigned`.
- `new <topic> --owner <name>` fetches and makes the copy from `origin/main`, never from the local main;
  `claim <path> --owner <name>` records the owner of a copy that exists; `sync <path>` merges `origin/main` in
  (refuses uncommitted work, never stashes, keeps a conflicted merge in progress and lists its files); `done <path>`
  removes a copy and its local branch only when its work is in `origin/main` and it holds nothing untracked. Nothing
  passes `--force` or deletes a file. Owners live in the shared git folder, outside the repository.
- `python -m ismail.handoffs` (the dev intake) ends with the same summary and the copies that need something
  (`--no-worktrees` skips it).
- New reference `worktrees.md`: the decision tree for a copy's whole life, each step naming the command. Linked
  from SKILL.md, development.md, contributor-dev.md and stage-dev.md.

### Skill: "the person" in the lessons

- The skill's lessons no longer name one user: each says "the person", as the rest of the skill does (blind-tests.md,
  mastering.md, phone.md, stage-takes.md, user-experience.md).

### Skill: shared references and a tidy after the skill review

- Two shared references: `loudness.md` (every output's level, and a table saying which limiter number belongs to
  which stage: planning, mixing, mastering, a new project's limiter, the live safety chain, a live set while it
  plays, the live output defaults) and `windows.md` (how a bar range, a deck and a live performer build a window,
  and the five rules for a voice, moved from `sound-design.md`). SKILL.md lists both, and the roles point at them.
- `listening.md` describes the zoom diff (at least three reference takes, level-matched, cells lit only outside the
  reference's own spread, a control take, spots, a closing check) instead of saying it is coming.
- `live.md`: a new "Highlights on praise" method (close the take, cut generously around the heard bar, mp3 and an
  index, delete a take only on the person's word); `phone.md` points to it. Autotune is the one effect with no live
  processor; a deck already plays its song's master chain, so that line is gone from "not yet".
- `music-video.md` is generic: articulation codes, cut cadence, the last bar of the final build, the ending and
  subliminals no longer describe one series. It links the lexicon, live highlights and `loudness.md` for the bed.
- `stage-takes.md`: the helper may use `stage_take_note` and `stage_actor_map_save`, and links rigged bodies and the
  lexicon. `stage.md` sends Blender and scripts outside the stage tools through the machine's GPU slot.
- `contributor-dev.md`: `git blame` dates a claim when auditing the skill, and a branch behind main gets main merged
  in by the dev, with each conflict explained to the person in their terms.
- `development.md` names `songs/_migration/LEDGER.md` in the roles table.

### Video: film renders take the machine's GPU slot

- `python -m ismail.video render` now takes the GPU slot on the shared machine board (`ismail.machine`) for each
  shot, in place of its own temp-file lock. The board's rules now hold for film renders as for any Blender render:
  one GPU job on the machine, the heat and disk floors, and no render while a live set is on air. Before, the render was invisible to the board and could start Blender during a set.
- It stands in line for the slot, up to 4 hours by default, to outlast a live set (`--wait 12h`, `--wait 0`
  refuses at once). A refusal says why, names the shots not rendered and exits 75, like `machine run`. The shot's Blender carries the slot
  (`$ISMAIL_SLOT`), so heavy work it starts runs in the same slot.
- music-video.md, SKILL.md and the README say so; the "machine-wide GPU lock" text is gone.

### Skill: rigged characters

- New reference `rigged-characters.md`: rigged human bodies on a VR or browser stage, from one long film build
  checked in a headset (MakeHuman as the worked example, the lessons marked for any rig): know your rig, rest vs
  posed export, wrists on a rig without twist bones, start poses as world frames, hands and fingers from hand
  tracking, props on bones, following a live person, the clip and view checks, materials that survive export.
  Listed in SKILL.md and linked from stage.md and music-video.md.

### Skill: the contributor dev

- The dev role for the engine is now the **contributor dev**: the dev on the person's own machine
  (`references/contributor-dev.md`, which was `maintainer.md`). It opens a pull request to the project once the
  person says yes to contributing; the project's maintainer reviews and merges. The skill no longer describes
  one team's setup.
- A studio agent that finds something worth contributing offers a contributor dev by its role's name, and starts
  the session itself where its harness can, so nobody is left with "the dev needs to do it".
- `contributing.md` drops the GitHub sign-up and install steps: the agent handles GitHub and asks the person only for
  what only they can do. The route without GitHub (a zip) stays.
- An area dev (the stage dev) sends its finished branch to the contributor dev as a local merge request, or opens
  its own pull request when none runs. During a live test the stage agent messages the stage dev directly and may
  start the stage dev session itself (`stage.md`, `stage-dev.md`, `development.md`).
- Internal record numbers and one team's names are gone from live.md, blind-tests.md and development.md.

### Skill: one line in user-experience.md

- The phone-first exams lesson, reworded; the lesson is unchanged (plan exams that work on a phone and earbuds).

### Provenance: every mp3 says which ismail made it

- Beside the made-with line and the link, each mp3 ismail writes now carries the version (`pyproject.toml`, 0.3.0),
  the short git commit (with `+local` when the package's code has uncommitted changes) and the skill's version: in
  the encoder settings frame for people ("ismail 0.3.0 (1a2b3c4, skill 0.1.0) https://...") and one frame each for
  tools (`TXXX:ismail_version`, `ismail_commit`, `ismail_skill`). They are read from the build at the moment of
  writing; a pip install without git or the skill folder writes only what it knows (hq:D-128).
- `python -m ismail.tags <file.mp3> ...` says which ismail made each file; with no file, which ismail this is.
- Versions are tagged on main from v0.3.0 on.

### Ops: `ops_find`, the tools by purpose and by role

- Agents could not find the tool that fit (Nate, 10-12, via the voice session): after a voice round he asked to see his
  voice against the renders, got static pictures he could not mark, and the tool for it (`exam_picture_round`) was
  found only after he described it from memory. `ops_find(need='show me the difference between my voice and the
  render')` now names it first. It searches every op's own description (a rare word counts more than a common one),
  so it cannot go stale; `ops_find(role='voice')` lists the shelves a role reaches for first; `ops_find()` lists the
  shelves (ledger:M210).
- Every op sits on a shelf (`ismail/api_ops.py`); a test fails when a new op sits on none.
- A sentence "Reach for it when ..." in an op's description counts as much as its first line, so an op says in the
  words people ask with when it is the answer. `exam_picture_round` has one, and `spectrogram` points to it.
- An unknown op name now says to look it up with `ops_find`.

### Exam pages: the picture round works on a phone

- A finger on a picture no longer drops a pin or a box (Nate, round s26: he touched a picture to scroll, got marks
  he could not take back). Each card has a Mark button: off, a finger scrolls the page as anywhere else; on, a tap
  pins and a drag boxes, as before. Mark starts off on a touch screen and on with a mouse.
- Every mark has a Remove button on its row, which takes the pin or box away (and its comment out of the answers).
  A removed mark's number is not reused.
- One card on screen at a time, with Back, Next and "card 2 of 5". An item can carry its own question,
  `ask`, shown above its pictures (`exam_picture_round(items=[{..., 'ask': 'Where does my voice go thin?'}])`).
- On a phone, pinning a comment no longer opens the keyboard over the picture; tap the comment box to type.

### Docs: the roadmap matches what is merged

- `ROADMAP.md` was last changed on 10-02 and still listed the stage as coming into ismail, though it has been in
  `ismail/stage` since 10-04 (Director, 10-12). Every row was checked against what is merged: the stage and what came
  with it are done, the exam op is building, several planned rows are building, and the phone page, sampled voices,
  auto-tune, effect stacks and the first session have rows now. The development checklist says to update a road's
  row in the pull request that moves it.

### Tests: the over-memory board test no longer depends on the runner's memory

- 10-12: `test_low_commit_holds_new_jobs_and_names_the_job_past_its_memory` failed once on macOS CI with
  `KeyError: 'over'`. It allocated 64 MiB and waited up to 10 s for the job meter to see it, but on macOS the
  holder's resident size sometimes never grew past its size at the slot's start, so the job was never flagged. The
  meter now reads a process's memory through one function (`machine._mem_of`), and the test sets what it reads, so
  it always sees the job 64 MiB past its declared memory and checks the exact numbers on the board.

### Tests: two phone tests no longer depend on how fast the runner is

- `test_a_note_the_speech_server_is_slow_on_waits_longer_and_lets_the_next_through` failed on Windows CI (main at
  61c568e, pr:195 on 10-12): its first note failed before the second note had been posted, so the line had one note
  and the retry went first. The first try now fails only once the second note is in line, as on the phone.
- `test_every_voice_note_keeps_the_music_under_it` failed on Windows CI the same day (pr:195): the guessed reference
  ends where the master ends, so it is shorter by however long the runner took to reach the second note (1.9 s
  there). The expected length now takes that time off.

### Measuring: a second part in a source is sensed, not assumed away

- New `stem_parts`: per band, how much of its envelope's movement sits at the source's note rate and the rate it
  moves at, the strongest pitch under 250 Hz per beat, and how many onsets are strums. A loud band that moves on
  its own gets a WARNING: another player, or bleed from another stem. A "guitar" stem that held a second player's
  bass line had wrecked a tone match (Spanish guitar handoff, 10-12); separation and "solo" stretches are not
  assumed clean (Nate, 10-12).
- `analyze_melody` ends with its confidence: pYIN voicing over the notes, and a WARNING when another pitch sits
  within 6 dB of a quarter of the notes or more, or when voicing is low. With a backing about as loud as the lead it
  reads the backing, as it did on that record; the warning now says so.

### Skill: sampling is allowed, and private use needs no caution

- Nate, 10-12: ismail samples like any DAW. Slicing a reference into the song is fine whenever the user wants it;
  private work (exact notes, blind tests with the record's clips, exams on the user's own phone) needs no copyright
  caution and no asking. Rules about what the user may do with music are suggestions, said once; the craft, quality
  and safety rules are unchanged. "Never place the reference in the render" became a craft default for building an
  instrument and for the side under test in a blind exam.
- `project_info` and `render` no longer warn about ref/ files without a SOURCES row. The row stays a habit for
  credits, and `credits` still lists what it could not credit.
- From the Spanish guitar handoff: a per-note pass before the first round with a played reference, a check of what
  a stem holds before tone matching (a second player's bass), testing the note reader on a known line with similar
  backing, a waveguide string feeding its body force rather than displacement, and the named recording as the target
  over any sample library.

### Phone exams: control pairs no longer fail their own pre-flight

- From the Sound Engineer (10-11): an exam of MP3 clips with `floor='auto'` was refused four times before it showed.
  The WAV control pairs read as "formats differ by class"; with three classes (the control pairs 'real' beside
  'record' and 'mine') the order check read 2-clip pairs as triples and saw "every trial starts with 'record'"; and
  every refused post spent the first round's full control set. Now the pre-flight checks the person's pairs only,
  `exam_check` reads trials by their labels (1A, 1B, 2A ...) whatever the number of classes, and a floor round
  counts only once the exam is on the phone.

### Phone: pictures on panels open full screen

- A tap on a panel's picture opens it full screen, as its videos already do: pinch or double-tap to zoom, a finger
  to move around it, and the cross, Back or Escape to leave (Nate, 10-12). Every panel picture gets it, with nothing
  to set up.

### Phone: a long voice note no longer freezes the server

- 10-11, 19:18 to 19:21 UTC: a 322 s talk note went through the hum check's pitch tracker, which took 150 s and held
  the whole phone server (other threads waited up to 129 s), so agents' phone ops were refused for about two
  minutes. The check now rules a note out by its words first (over one a second) and skips notes over 60 s, so talk
  never reaches the pitch tracker. A hum is still told apart the same way.

### CLI: answers no longer fail on a Windows console

- 10-11: one sharp sign in a phone hum row made every `python -m ismail phone_listen` from before that row print
  only "ERROR 'charmap' codec can't encode", because a piped Windows console is cp1252; a watcher retried for about
  an hour and the page never showed it listening. On a console that isn't UTF-8 the CLI now writes plain ASCII,
  with any other character as a JSON escape (`\u266f`), so a JSON answer still parses and every reader can decode
  it. A UTF-8 console is left alone, and so is the MCP server's stdio.

### Phone: a slow speech server no longer buries voice notes

- 10-11: a 213 s note timed out at 120 s while the speech server went on with it, and every retry added another
  copy (about 40 in two hours), so neither it nor the short note behind it was transcribed. Now each try waits
  twice as long as the last (up to an hour), a note that failed goes to the back of the line, and after a restart
  the notes of the last day still without their words go back in line. A note's words written beside it as
  `<id>.txt` are used instead of the speech server (a note transcribed elsewhere).

### Stage: props pinned to a person's bone

- `stage_prop_pin(scene, object, person, bone)` pins a prop (sticks, a guitar, a bass) to a bone of a person's body
  where it stands now. It then rides whichever body the person shows (resting in a start pose, playing a take,
  following the user) and stands where it was when they show none. Saved with the scene in world.json `props`; the
  edits file keeps the prop where it stood before the pin, and `clear=True` puts it back there. A wrong bone name
  lists the body's bones. (The user, 2026-10-10: a drummer's sticks and a player's guitar stay in their hands.)
- Two code comments that named scene characters are generic now.

### Auto-tune: a voice moved onto notes, its character kept

- Playbook: where `autotune` goes in a vocal chain (first, on the dry take; sound-design.md), and in a live set
  tune to the set's key, the three settings that cover most asks, and tune the file before playing it (live.md).
- New effect `autotune` (Nate 10-10): WORLD splits the voice into pitch, envelope and breath, moves only the pitch
  onto `key` + `scale` or onto the notes of the track named in `notes_from` (in the singer's own octave), and puts
  the voice back together. `speed_ms` is the retune speed (0 = the hard robotic snap; slower keeps vibrato and
  scoops), `amount` corrects part way, `formant` moves the voice's character without its pitch. Studio first: in a
  live set it is baked. Built on the Voice agent's guidance: the WORLD round trip passes blind, and what sounds
  "autotuned" is a rule-made pitch curve, so quick pitch moves pass through unless the speed is 0.
- New op `sound_autotune(path, ...)`: the same on a take on disk (a phone voice note or a mic take), written beside
  it. live.md: sung takes tuned and played back in a set.
- WORLD is optional: `pip install 'ismail[voice]'` (pyworld); the effect says so when it is missing.

### Stage: a person's box is where their body is; a test copy never takes a scene's ops

- A person's selection box, ray pick and hand pick follow the body they show (resting in a start pose, playing a
  take, following), not the hidden statue it stands in for. A person with a start pose stood about 30 cm in front of
  their box, and the hidden statue's meshes caught every pick inside the old box, so what stood there could not be
  taken (the user, 2026-10-10). Small things inside the body's box (a bottle on the bar in front of them) are picked
  before the person.
- Nothing hidden takes a pick any more: a mesh counts only when it and every parent are shown.
- When two stage servers have the same scene, ops go to the one whose scenes folder is not under a hidden folder (a
  test server's `.scratch` copy took every agent's ops for the real scene for ten minutes).

### Stage: a Follow from a start pose takes the user's hands, shoulders follow the arms, labels fit

- At GO, a person with a start pose now has the user's own hands and fingers, placed from the eyes and turned to the
  person's facing, instead of the start pose's hands moved by the user's change since GO. A posed start (a drummer
  holding sticks) had the fingertips twisting up and the hands reaching from the wrong place (the user, 2026-10-10:
  "the starting position of the hands is not reset to what my hands' position is when we start").
  `stage_actor_start(hands='pose')` keeps the old way, for a grip that must hold.
- The shoulders take a share of the arms' swing (30%, at most 25 degrees), so a reach no longer pushes the arm
  through the body. Forearm twist bones, where a body has them, share the hand's turn about the forearm.
- Name tags, load set labels and the countdown shrink their text to fit instead of cutting it off.

### Stage: a running take always has a stop beside you

- While a take records, a small "■ Stop take" panel rides beside the person until the take stops, however it was
  started (the menu, the REC button, an agent). A Follow's own panel already has one, so it is not doubled.

### Stage: pinned hips hold over a start pose, a failed Play says why, captures wait for everyone's pose

- A person whose hips are pinned to a seat stays on it in playback and in a Follow when they also have a start pose
  (the start pose kept its own hips before, so a standing start pose stood them up).
- Play on a person with no body to play on now says so in the headset, and that the take is kept, instead of doing
  nothing.
- `stage_capture` waits for every person in the room to be in their pose before frame 0 (up to 60 s), and
  names anyone who was not in the capture's status.

### Stage: hands over menus, menus you open never wait, sign-offs on the right thumbs up

- Hands show in front of a menu they reach into, and stay hidden behind one in front of them. The pointer beam, its
  dot and the travel arc draw over the room and over menus. Back to front: the room, menus, hands, beams.
- A menu you open yourself shows at once, even during a voice note; only an agent's panels and speech wait while you
  talk. Nothing times out or deselects while you talk.
- A voice note's `during` now lists the menus shown, the buttons pressed, why a panel closed and presses on things,
  beside selections and touches.
- An agent's question (`stage_ask`) or panel waiting for an answer no longer holds the other agents' commands. The
  skill says every sign-off in the headset is a right thumbs up through `stage_ask`.

### Phone: a voice note says what was playing and what they did

- A note carries `media` (each video and clip on the open panel when it began: file, the second they were at,
  length, playing or not) and `acts` (what they did on the page while it recorded, timed from its start), on its
  `voice_text` row and in the exam's answers file. A panel video's play, pause and seek are page events now.

### Stage: a pinch on a thing whose menu is open does nothing

- A thing with a menu keeps the one it has up: another pinch on it while the menu is open leaves the menu where it
  is, so pinching it over and over no longer closes and reopens the menu each time.

### Phone: the music leaves call quality when a note ends

- The level meter's audio context now closes with every voice note. Left running, it kept Android in call mode
  after the note, so the stream played on in call quality through Bluetooth earbuds.

### Development: cleanup is part of every merge

- development.md and maintainer.md: whoever merges a pull request removes its worktree, local branch and merge
  copy right after; the repository now deletes head branches on merge. 160 worktrees and 185 branches had piled up
  because removal waited on a yes that was never asked for.

### Phone: an exam's voice notes land in its answers file

- A voice note said on an exam card is written into that exam's `answers_path` (kind 'voice_note': text, audio
  path, and the box it was said into), even when it is transcribed after the answer was sent. Before, it went only
  to the inbox and the answer's empty note read as "no comment".
- Every text box on a card or panel has a mic; the words fill the box, editable, and the answer carries the
  recording (`note_audio` and `note_source` on an exam, `values_audio` on a panel). An answer sent while a note is
  still being transcribed says so (`voice_notes_pending`).
- `phone_exam` replies with each clip's url, path and length, and where the answers and notes will land.

### Stage: many keys in one call

- New op `stage_keys_set(scene, keys, replace, interp, save)`: a whole key list for any objects in one page
  command, from values (location, quaternion, scale, or `look`: aim a camera or light at a point), without moving
  the object to each spot first. Every key is checked before any is written; the clock's span grows to take keys
  past its end. `look` is stored as a quaternion (`interp.js lookQuat`, `interp.py look_quat`, held equal by a test),
  so renders read it as any key.

### Stage: capture a shot on the PC, frame-locked

- New ops `stage_capture` and `stage_capture_status`: a scene, a camera (a camera object, a story path from a
  cameras.json, an inline path, or a fixed view) and a song time span in, an H.264 video (and PNGs if asked) out.
  Frame n shows the song at t0 + n / fps: the page's clock stands still between frames, takes played on the music
  follow the song time, and the stage clock is set every frame. It runs in the background with its own server and a
  headless Edge or Chrome, on the GPU slot, with no headset; the capture's server answers every write "not saved"
  and passes no live commands, so the scene files and the stage in use are never touched. Every load set is in
  the shot (the headset unloads some to stay light) unless `sets=False`.
- The outline of an unloaded set is a helper now (the editor and headset see it, a capture or a camera view does
  not).

### Stage: a quick pinch presses, a held pinch opens the menu

- A thing with both a press and a menu ran its press AND opened its menu on every pinch, and every pinch stacked
  another menu (the user, 2026-10-08, on a glowing orb). Now a quick pinch presses and a pinch held for 0.45 s opens
  its menu instead; a thing with only a menu opens it on a pinch. Each thing has one menu: a second pinch replaces it.
- A behaviour's `move` turn on two or more axes now matches Blender's XYZ order (it composed the axes the wrong way).
- Behaviour tests are back with a generic switch, and the stage tests use generic names only.

### A finished job leaves the board even while another session reads it

- `machine.slot`: Windows refuses to delete a file another reader has open, and every session reads the job board.
  A slot that ended at the moment a waiter in line read its job file stayed on the board, alive, for as long as its
  process lived, and the next job in line sat out its whole wait behind a job that had finished (the priority test
  failed 1 run in 5 on a Windows laptop: vox's instant job held the GPU slot 60 s). The job file, the waiter's
  place in line and a cleared priority are now removed with retries until the reader lets go (2 s at most, then a
  warning). Test: a reader holds the job file open while the slot ends, and the board is empty after.

### No song content in the tests

- Removed tests/test_stage_behaviours.py: it tested one song scene's light switch, which is the user's VR content
  and never part of main. skills development.md, the inclusion review, gains a fifth question: tests on main use
  generic names, never an object, scene, character or take from the user's songs (Nate 10-09).

### A first session ends with the song in hand and a question

- `guide`'s first session opens with two short plain sentences on what this is (every note and sound written by
  the agent, a song changed one piece at a time, any part of music at their level), says to hand every sound over
  (the file attached or linked, never only a sound through the speakers once), and never to end on a setup report
  or to replace a sketch with hand-written notes. Live play is offered once they like or keep a song. A plain-words
  session also gets the "one piece changed, the rest kept" moment.
- `sketch`: the first round's SAY block says it is a first try ("I don't know your taste yet") and asks what they
  would change or for a song they love; a HAND IT OVER line names the file. `sketch_keep` offers live play.
- New op `play_file(path)`: opens a sound in the person's own player (Windows, macOS, Linux).
- skills: step 8, setup's last step ("the end of setup is their first song, not a report"), user-experience.md
  (Nate 10-08, after a Codex dress rehearsal ended on a setup report with the song buried in it).

### Stage: a Follow that sets itself up from the body

- A Follow on a standing person now walks with you by default; a seated one (hips pinned to a seat) stays on it. A
  standing man came up "with his feet locked" and would not walk (the user, 2026-10-08). The cause was deeper: a
  person with a start pose was followed by a path that kept the start pose's hips and legs whatever the user
  did, so neither the walk nor the button moved him. That path now walks, turns and steps too, from the start pose.
- Head moves stay in the upper body: the hips hold still while the head moves within 12 cm of above them and the body
  turns only when the head turns more than 35 degrees from it, so a lean or a look aside bends the spine and the feet
  stay put ("when I move my head ... sometimes the rest of my body moves, but that's not actually what's going on").
- The Follow panel has four buttons (Stop, Walk with me or Stay on the spot, Turn, More) and says the setup in a
  sentence; mirror, pinning the hips, the mic and moving the spot are under More.
- `stage_actor_follow` leaves `mode` to the body unless given, and replies with `body`, `mode` and `says`.

### Stage: a thing whose code fails says so

- Every behaviour failure, at load or on a press, goes to the agents as `behaviour_error` with its phase and the line
  in behaviours.js (a file that does not parse included), so whoever wrote it can fix it at once (the user,
  2026-10-08: "you should have an event hook on object functionality errors").
- In VR a press that fails buzzes and shows a short note at the object, and a thing whose code did not load does the
  same instead of opening the edit menu: a broken switch no longer looks like one that is off.
- An async function that fails is reported once (it was reported twice).

### The zoom is easy to find, and says when to use it

- `spectrogram` and `exam_eye_crops` say when to reach for the zoom: a texture word from the person (robotic,
  digital, smeared, cut off), numbers that say close while the ear says no, voices and played instruments, two
  rounds without progress, and checking that a fix made its spot fade. `sound_compare` and the `cmp` summary end
  with a one-line pointer to it.
- skills: the loop's Listen step names those triggers; listening.md drops "last resort" and gains "When to zoom",
  with what the person said and what the zoom found on the voice work, and the shared words for a spot (bands,
  shapes, movement) from the Voice agent (Nate 10-08).

### Stage: things that do things, from saved code

- A scene can give its objects their own small functions (`scenes/<scene>/behaviours.js`, written by an agent): a
  press, a menu, inputs other objects can wire to, a state saved with the scene, and `apply` mapping state to the
  room. The page runs them itself when the person pinches or triggers the object in VR, so a light switch flips the
  lights at once with no agent listening (the user, 2026-10-08). The functions get a small handle on the stage:
  lights (with fades), show and hide, moves and turns over time, sounds, any page command, timers, wiring, and
  messages to agents. What a behaviour changes is never saved as an edit or into the Blender build.
- Ops for agents: `stage_behaviours` (what is defined, load errors and warnings; `reload=True` after writing the file),
  `stage_behaviour_run` (the same press or menu item the person makes, with its sound) and `stage_behaviour_state`.
- Every interaction sounds: an object's own `sound`, else a click and a warning to the agent. The stage reference
  says how, and where the line between the stage and the agents is.
- A pinch still held from one menu press no longer presses the menu that replaces it (one press on Walk with me fired
  three answers and the person stayed put).
- A scene switch closes the void in slowly (1.6 s, eased) and dusky, instead of the white flash.

### Stage: one body per person, and lights that start at 0 W

- A person with a start pose no longer shows twice when a take or a Follow starts while something already plays on
  them (the user, in VR, 2026-10-08: a person showed twice while following and recording). The stop a play begins with set their resting
  body loading, and it could finish while the new play was still being set up. A play or Follow being set up now
  claims its person, and a rest still loading stands down; if the play fails, the person rests again.
- `stage_light_set` on a light saved at 0 W no longer divides by zero (it answered `energy: null` and the whole room
  went black). Watts convert through the light's own ratio, or for a 0 W light an area light's size or the ratio of
  the other lights of its kind. A light set to a negative or non-finite energy is refused.
- NaN and Infinity never reach the page: the agent link refuses them with a clear error, and the server answers 400.
  The page cannot parse them, and its command queue stalled.

### Two waiting jobs no longer take one slot together

- `machine.slot`: a job goes on the board in the same lock as the check that let it start. It used to be registered
  after the GPU, disk and memory readings, and meanwhile the next job in line saw a free slot and started too (two
  jobs on one GPU slot; it showed as a macOS CI flake on main 10-07). Test: two waiters, a slow reading, never two
  jobs at once.

### Song rebuilds back up only what cannot be made again

- `ismail.backup`: `set_aside(proj)` moves the old `proj/` to `backups/proj_<time>` as before, and `carry_back(kept,
  proj)` moves its `cache/`, `renders/` and `stems/` into the new `proj/` instead of leaving a copy in every backup
  (13 rebuilds in 15 minutes had written 3.2 GB, Machine Steward 10-08). Older backups that still hold those folders
  hand them to the song's `_reclaim/backups/` (the newest 2 keep theirs; never deleted). `python -m ismail.backup trim
  <backups> [--keep N] [--dry]` tidies backups made the old way. The skill's song layout says to use it.

### Docs: a news feed between agents

- skills phone.md: in a team, plain news for the lead goes to a feed it reads at its check-ins; a direct message is
  only for a decision, a blocker, a conflict, or something the person must hear now (Nate 10-07).

### Docs: how an agent watches the phone

- skills phone.md: one `phone_listen` loop that filters nothing by owner, passing untagged notes on, real silence
  during a listening exam (stopping tracks, not moving the output), re-arming a watcher before its host stops it, and
  times in the person's own zone (Nate 10-07, from the Live DJ's working loop).

### Phone: exam rounds are marked, and the page's state is in phone_status

- When the page opens an exam panel (or a panel linking under `/eye/`, the picture round), the inbox gets one line
  of kind `exam_round`, state `started`, with the panel id, `for` (its sender) and title. When it is answered,
  dismissed, set aside or closed, or another panel takes its place, one line with state `finished`. Never twice for
  the same change. `phone_listen` returns these lines by default (not only with `page=True`), so a listener that
  plays sound can mute on `started` and unmute on `finished`.
- `phone_status` adds the open panels (id, sender, priority, kind), the panel the page shows now and since when,
  "exam in progress: <panel> for <sender> since <UTC time>" (or "none"), and the last inbox line number, which is the
  `since` for `phone_listen`. The existing lines are unchanged.
- `phone_listen` says it is the watcher: every line, never filtered by owner, the agent decides what is its own.
  skills phone.md says to watch with `phone_listen` and `phone_status`, never filter by owner in a script, and go
  quiet between `exam_round` started and finished.

### Phone: who gets a voice note nobody tagged

- skills phone.md: a free voice note plainly about another agent's work goes to that agent at once, verbatim, with
  its inbox number, id and time; an unclear one is kept and the person asked (Nate 10-07: "you should always send to
  an agent I've already asked you to work with before").

### Phone: the picture round opens on the phone

- The phone server now passes everything under `/eye/` through to the picture-round page server (127.0.0.1:8871),
  with its body, Content-Type and Range, so the eye exam page opens on the phone's https address and its sounds
  seek. With no round open it answers 503 "No picture round is open right now."
- `phone_panel_show(link='/eye/<round>?from=phone', link_label='Open the picture round')` puts a big button in the
  panel that opens a path on the phone server in the same tab. Anything that is not a path starting with one `/` is
  refused. `exam_picture_round` says so in its reply.
- The exam page (`words.html`) has 48 px controls on a phone, a "Peek (hold)" button for the Shift peek, plain-words
  hints instead of key names, a drag that is dropped cleanly when the phone cancels it, and a "Back to the phone"
  link when opened with `?from=phone`. Nothing changed on a desktop, and its ids and answer format are the same.

### Phone: a video on mobile data starts at once, and says when it cannot

- A player asking for the rest of a file gets it 2 MB at a time, so the first frames of a long video arrive at
  once on 4G (Nate 10-07 15:39: a 4:43 video "not loading on the phone"). A transfer that drops is one line in the
  server's log.
- A panel's video says "loading", then "still loading on this connection" after 15 s, or that it could not play,
  each with a link to open it on its own, and reports `video_ok`, `video_slow` or `video_error` to the events.

### First contact: hand-holding for plain words

- Someone on plain words no longer gets a test. `sketch` tells the agent to play version 1 at once with one line of
  delight; the other sketches are spares, for when they want something different. No "which is closest", no "what is
  missing", no three to compare. Musicians keep the rounds they had.
- Every round now ends with two or three playful choices in everyday words, made from that very sketch, plus "or tell
  me anything" ("Want it different? Try: add a beat you can nod to, faster and bouncier, or a bit spookier, or tell
  me anything."). `sketch.playful_choices` picks them and checks each one against `apply_words` first, so every choice
  changes the sketch when it comes back as the next round's words. New feeling words: bouncier, bouncy, peppier,
  perkier, zippier.
- A change plays at once, new version first, with one plain line on what changed, and now and then the agent slips in
  a small unasked flourish and names it after it plays. "Want to keep this as your song?" leads to `sketch_keep`.
  Live changes are not offered yet.
- `guide`'s FIRST SESSION block now has a musician branch and a plain-words branch (also returned by
  `guide(first_answer=...)`). AGENTS.md and `setup.md`: never say skill, plugin, MCP, uv or server to the person unless
  they do first, and a plain chat that cannot run commands says so in one sentence and names what works.

### Phone: scrolling past the talk key never starts a note

- The big talk key records only on a still press. A touch that moves (past about 10 px, or that the browser takes for a
  scroll) scrolls the page and records nothing; a quick still tap is still hands-free (Nate 10-07 15:13).

### Phone: panels can carry a video

- `phone_panel_show(video=<file>)` puts a video in a panel. It plays inline on the phone page, next to the panel's
  buttons, inputs and "Say more" voice reply (the video pauses when they start to record), so the person can watch it
  and ask questions about it. Any agent that controls the phone may use it.
- The agent's process first makes a phone-sized copy (H.264 and AAC MP4, at most 720 px tall, fast start) inside the
  machine's cpu slot, so it waits its turn like any heavy job (`video_wait='10m'`; a busy machine comes back as a
  plain message). The copy is kept in the phone's `videos/` folder, so one video is converted once. A small H.264 MP4
  is sent as it is. Without ffmpeg the error says what to do.
- The phone server's `/files/` now answers HTTP Range requests (206), which phone browsers need to play and seek a
  video, and knows `.webm` and `.mov`.
- Later on a panel only hides it: the corner key stays lit and keeps counting it (it went grey while a question was
  still open).
- `phone_panel_show` and `phone_say` take `priority`: 'needs you', 'normal' (the default) or 'low'. Notes and panel
  tabs sort by it (needs you first, then newest), and the corner key fills with the accent colour while an open item
  needs them.

### A newcomer's next round keeps their tune (ledger:M181, intro dry run 2)

- `sketch(..., base=)` keeps the picked sketch's tune and chords and changes only what the words name: the first
  take is the change, the second the change taken further (it was two new tunes, one of them sparser after "a bit
  happier"). The reply says the words back ("your version 3 with the same tune and chords, happier: faster,
  brighter") and names the before and after to play. `sketch_keep` and the guide now give the same route for a
  change.
- Version numbers go on across rounds (a next round after three starts at version 4), and nothing at the top of the
  song folder is replaced, so "play version 3 again" still works. The kept song is `song (version 3).mp3`, not the
  folder's name. `sketch_wait` and `sketch_keep` name versions, not letters.
- Plain-words versions say how they differ: a key change of the same mode is "pitched higher" or "lower" (it read
  "brighter"), "fewer notes" or "more notes" for sparser and busier, and the sound and mood are said once.
- An upright, saloon, player or honky-tonk piano in a brief is named as missing, with the grand piano standing in,
  and is on the showcase's "not covered yet" list.
- A sketch is checked after its loudness trim: a tune pushed to full scale brings the master down until it peaks
  under -1 dB (version 3 had reached -0.0 dB).
- `sketch` stands in line for a busy machine (`wait='2m'`), and a refusal opens with BUSY and the plain words to say
  ("the computer is busy, I'll try again in a moment") before the details.

### The live safety can no longer silence a set

- One huge but finite sample drove the level rider to -423 dB and the set was silent for minutes (Live DJ 10-07 12:31). A sample past +30 dBFS is now zeroed like a NaN, the rider's level reading is clipped, and the rider never pulls deeper than -40 dB.
- A track whose live effects blow up (NaN, inf, or past +30 dBFS) has that block silenced and is named in live_status news, once a second at most.

### Phone: the controls come first, and every note is one tap away

- Listen, talk and the keys sit at the top; "Since you left" and the latest note move below them, the summary three
  lines until tapped (Nate 10-07: "this could be a little bit more ergonomic").
- The corner key counts notes not seen yet ("1 question · 2 new"), including one that came in during a voice note,
  and opens a Notes list (the summary and the last twelve notes, the new ones marked) beside the open questions.

### Phone: answered panels close at once; the server's log is dated

- An answered panel closes on the phone at once, without waiting for the next state (Nate 10-07 12:01: a panel stayed open after his answer and he could not get out of it). A refusal from the server (the question already closed) is shown and closes it too, instead of being queued as offline and retried.
- Every line of the phone server's log starts with the UTC time, and a phone dropping off mid long-poll is one line
  ("dropped: ConnectionAbortedError") instead of a traceback.

### The phone sets where the sound goes (ledger:M179)

- The phone page shows the set's output and a Follow Windows switch: on moves the set to the Windows default output
  and follows it (a headset plugged in takes the sound), off keeps it where it is. It acts on the live engine
  directly, so no agent is needed; each change reaches the inbox as kind 'output'. `live_device` takes
  `reopen=False` to change following without a gap.

### A new person's words stay theirs (ledger:M175)

- On a machine where songs were already made, `guide(first_answer=...)` no longer writes the owner's machine-wide
  vocabulary: a new person's record goes beside their song (`person.json`, marked as a guest's), or waits in
  `~/.ismail/pending_person.json` (12 hours) until their first `sketch` moves it into the song folder.
- `lexicon_note`, `lexicon_find` and `lexicon_view` on a guest's song read and write that song's own
  `lexicon.jsonl`, so a guest never adds to the owner's lexicon or sees the owner's words. Marketing's dry run 2
  found both.

### Named effect stacks, with what they cost on this machine (ledger:M177)

- `fx_stack_save`, `fx_stack_load`, `fx_stack_list`, `fx_stack_measure`: an effect chain saved by name with notes,
  in the library (`~/.ismail/fx_stacks`) or a song (`<song>/fx_stacks`); `live_track(fx='stack:<name>')` uses one.
- Saving measures 10 s through the chain the way the live engine runs it (live processors in 1024-sample blocks,
  baked effects over the window) and keeps the times-realtime figure per device. `live_track` and `live_load` name
  each saved chain they meet with its cost here, and RISK under 4x.
- Five built-in stacks from the 10-07 freestyles: `kit70_glue`, `dirty_pbass`, `afrobeat_tenor`, `afrobeat_chank`,
  `tonewheel_organ`.

### Phone messages never pop up

- Panels and exams no longer open over the page (Nate 10-07: "these are modals that block me"). A new one buzzes
  once and shows on a corner key ("2 messages"); he opens it when he chooses, switches between open messages on
  tabs, and Later puts one back. The rest of the page stays usable.

### Phone panels wait their turn

- A new panel or exam no longer covers one the person is reading: it waits until the open one is answered or set
  aside, the open one says another is waiting, and `phone_panel_show` replies that it is queued (Nate 10-07: "this
  test interrupted my reading of another panel").
- Every panel has Not now: it arrives as a dismissal (`dismissed: true`, answer null), not as an empty answer. A
  choice input must be picked before a button sends, unless it is `optional` (Live DJ 10-07: Send with nothing
  picked came in as {"which": null}).

### Clarinet, bassoon and French horn in the showcase (ledger:M169)

- Three measured voices from VSCO 2 CE (CC0) passed a blind ear check against the real recordings: `clarinet`
  (not told apart), `bassoon` and `horn` (good enough). New families `winds` and `brass`; `sketch` now plays a
  clarinet, bassoon or horn when the brief names one instead of standing the violin in, and "brass" gets the horn.
  Flute, oboe, trumpet, trombone and tuba were measured too but heard as synthetic: they stay out until they pass.

### Feeling words change a sketch (ledger:M170 S-6)

- `sketch(base=...)` reads happier, sadder, calmer, more exciting, darker and dreamier (and their near words) as
  concrete moves: tempo, major or minor, busier or sparser, softer or brighter; "a bit" halves the move, "much"
  makes it bigger, and the reply names each ("happier: faster (95 BPM), major, busier, brighter"). Marketing's dry
  run: "a bit happier" changed nothing and two takes came back slower.

### The picture round: the eye exam page (ledger:M165 step 2, M12)

- `exam_picture_round(out, items, title)`: vox's word-picture page in main. One sound per card, the real picture and
  ours of the same window stacked (F flips, Shift peeks, B blinks), M / S / 0 about the picture showing, comments
  pinned at a time and frequency (a point or a dragged box), the key served only after a submit. Real-first and
  real-second are balanced and seeded. Pictures come from `spectrogram_png`'s eye mode (step 1).
- `exam_picture_score(out)`: per card right, WRONG, cant or none, with flips, blinks, plays, the note and each pin.
- `ismail.exampage` serves rounds (`python -m ismail.exampage serve <folder>`) with HTTP byte ranges, the hosted
  round of M12. Touch works too (pointer events).
- Parity: vox's answered rounds w1 and w2, moved into main's round format, score line for line the same as vox's
  work/score_words.py (11 and 101 lines).
- README: the tool count is 221 (113 music and live, 88 stage, 20 phone).

### A part at full scale is named (ledger:M170 S-1)

- `render` lists HOT on any track whose stem peaks over -0.5 dB, with how far to lower it. Marketing's dry run had
  a melody at -0.0 dB and no word of it (the master was fine, the limiter flattening the tune).
- The sketch balance never raises the tune past a -1 dB peak; when it needs more, the other parts come down.

### The person's words are kept, and versions play from the song folder (ledger:M170 G-2b, U-5)

- `guide(first_answer=..., project=)` keeps the vocabulary it decided (`<song>/person.json`, and the latest person
  beside the first-session mark). For plain words, `sketch` says "Version 1: a piano plays the tune, slow and
  bright, about 30 seconds" (no keys, BPM or bars, also in its change lines), and `sketch_keep` offers the next
  change in their words, not "a warmer bass from bar 5".
- Sketches are "version 1, 2, 3", starting again each round; `base=` and `sketch_keep` take the number. Each
  finished sketch is also copied to the top of the song folder as "version N", replaced by the next round's.
- The kept song gets a playable file at once (`<song>/<song name>.mp3`).

### A newcomer is a person, not a machine (ledger:M170 G-3)

- `guide(new_person=True)` opens with the first session for someone new at a computer where songs already exist
  (Marketing's dry run and the DJ's fresh-agent test both got no first session there); the plain `guide` reply on
  such a machine names it in its first line.
- `sketch_keep` marks the first session done only when the machine itself was new, so a guest's keep writes
  nothing for the owner.

### Floor pairs in phone exams (ledger:M173)

- `phone_exam(..., floor='auto' | 'full', floor_from=)`: device floor pairs, the real clip against a 64 or 128 kb/s
  MP3 copy of itself or against itself, keyed so neither side is the synth. The full set on the first floor round,
  then one rotating pair (history in the phone home, `floor_rounds.json`); seeded places, never first; neutral file
  names. The pairs are renumbered; the reply and `<answers_path>.floor.json` map them back. Reference: the Voice
  session's work/phone_round.py (hq:D-60).

### Three measuring rules (ledger:S56)

- listening.md, "Readings that mislead": a sum of medians runs short (fit lengths on a log scale, lengthen phrase
  ends, put pauses back); level and band charts cannot see phase (a blind pair for any timing or phase stage); one
  render is not a result (two seed sets before claiming a difference). From the Voice session's lessons (hq:D-60).

### Recording warnings before a take is measured (ledger:M172)

- `ismail.capture`: two capture faults found before a recording is measured. Peaks flattened into a plateau under
  0.98 (the browser mic chain stops near 0.894-0.899, so a clip test never fires; more than 20 samples a second
  within 0.006 of the maximum), and band-limited capture (8-12 kHz more than 60 dB under 0.5-2 kHz: a Bluetooth
  headset mic). From the Voice session's lessons (hq:D-60).
- `mimic_measure` lists RECORDING WARNINGS per take and keeps them in the profile (`capture`); `no_top=True` for an
  instrument with no top. `sound_import` warns the same way, before normalizing. Phone hums carry `capture` and
  `air_db` in their check and the inbox record.
- Parity: air_db equals vox's rec/server.py air_db to 0.0 dB on all 39 of its r39 (Bluetooth, all flagged) and r45
  (clean, none flagged) takes.

### Phone panels: choices, checkboxes, toggles, a voice reply, and never over a voice note (ledger:M167)

- `phone_panel_show(inputs=[...])`: choice (one of), check (any of), toggle and text inputs beside the buttons; the
  answer carries `values` and `for` (the sender). Nate 10-07: "checkboxes or toggles so that I could give more of a
  detailed response".
- Every panel has "Say more": tap to record, tap to send; the note carries `panel` and `for`, so it reaches the agent
  that sent the panel, and the panel stays open ("I felt like I should be able to say more").
- A panel that arrives while a voice note records waits, silent, until the note is sent; the talk hint says one is
  waiting (Nate 10-07 08:56: "Am I still recording right now? ... it was still recording geez").

### First session: the rest of ismail waits for its moment (ledger:M168)

- guide's first-session block (step 8) and user-experience.md: the phone page, live play and the VR stage are each
  offered in one sentence at the moment they answer something the person did (kept a sketch, wants to jam, has a
  headset, steps away), never up front; a no holds for the session.

### Phone page: catch up only while the phone holds sound ahead (the 4G cutouts)

- The page played 1.08x to catch up with the room whenever it was over 15 s behind; on 4G that drained its buffer and
  stalled every 4 to 9 s (Nate 10-07: "cutouts on the phone that are not on the speakers"; 32 stalls in 45 min, all at
  1.08x). It now speeds up only while 4 s are buffered, drops back under 2 s, and holds normal speed for 2 min after a
  stall. Each stall logs the rate and seconds buffered when it began.

### Hum a part into a voice note (ledger:M163, the Live DJ's handoff 52)

- The phone server keeps the last 150 s of the master in memory while an engine plays (page open or not), and every
  voice note saves the music under it (`<id>_ref.wav`, 3 s before to 2 s after, with beat stamps), so the bleed into
  the mic can line a sung part up with the beat heard, with no recording on. Heard from the room speaker with the
  stream off, the start is a guess from the note's arrival and the ref spans 20 s either side.
- A hum is told from the note itself, with no button (Nate): pitched, holding its notes, few words. It reaches the
  inbox as kind `hum`. `phone_hum(voice_id)` answers for any note.
- The page records raw by default ("Mic: raw"; "Mic: cleaned" turns the phone's echo cancelling, noise suppression
  and gain control back on): the processing stripped the bleed on Nate's first hum. Each note carries `mic` (the
  route and what the browser applied) and how long ago it ended, so an upload that waited offline still lines up. The notes, grid and swing come from the Live DJ's
  measuring once it holds on real hums. 213 tools.

### The phone page stays awake while the stream is down (ledger:M142)

- After a server restart a page in a pocket never reconnected: a hidden page whose audio stops is frozen by
  Android, so its retries never run. While the stream is down (an error, a restart notice, a stalled stream), the
  page now plays a loop far under hearing (40 Hz at -80 dBFS) until the stream plays again. Needs a check on a
  phone with the screen off.

### Spectrogram windows for eyes (ledger:M165 step 1, moved from vox)

- `spectrogram(seconds=, f_lo=, f_hi=, ruler=, words=)` and `analysis.spectrogram_png(...)`: one sound on a fixed
  plot box (two pictures of a window line up pixel for pixel), a ms ruler, words drawn and named, and a `.json` map
  from pixel to (s, Hz); `analysis.eye_address` names a spot the way the person and the agent both read it. Parity
  with vox's `eyeword.crop_png2` and its page's address: 20 of 20 pictures identical on its own files, every click
  on the plot box the same address. The mel view with bar lines is unchanged.

### Skill: loudness matching is one fixed gain, never ffmpeg loudnorm as a filter (ledger:S55, the Live DJ's handoff 51)

- instruments.md (A/B clips) and mastering.md: measure, then apply one fixed gain; `loudnorm` as the filter rides the
  level, even two-pass with `linear=true` when the true-peak ceiling would break.

### The phone page shows the set's notes as they reach the ear (ledger:M160 phase 2, Nate 10-06 14:48)

- `phone_vibe(react={track: reaction})`: flash, glow, burst, sparks, drops or ring on every note of that track
  ('qrq*' matches a prefix), each fired when the phone hears its beat (the stream's delay included). The engine's new
  `onsets` command lists the events it has placed (track, beat, level); the phone server passes them on only while
  a look reacts to them. No audio analysis on the phone.

### First session: one opening, the person's words, calmer setup (ledger:M151 G-1, G-2, U-1..U-4)

- One opening: guide's FIRST SESSION block is the only one. SKILL.md's step 0 no longer asks for a recording first
  (one is welcome, never required), and the skill's first-session section, README, AGENTS.md and setup.md point to
  the block. At most two questions before any sound: what it is for and whether they play, then a mood or a
  reference.
- `guide(first_answer=<their words>)` says which words to use from then on: musician (they name an instrument they
  play, a style they trained in, or reading music; a negated mention does not count), with their trade's words, or
  plain words. user-experience.md has the rule.
- setup.md: before the first command, "about 10 to 20 Allow boxes" (how it was counted is in a comment beside it);
  the plugin added from the shell when a `claude` command is there, otherwise both lines in one block with one
  sentence, or one line on Claude Code 2.1.275+; "Quit Claude from the tray" on Windows; Documents found through
  the known-folder path (OneDrive moves it), replacing the `%USERPROFILE%\Documents` setx line; every wait named
  with its time from the dress rehearsals (install, warm-up, first call, first sketch). The first-session block
  says the sketch times too.

### The update card lists every change waiting

- The left-wrist update card lists the changes waiting (up to five, the most important first, then "+N more") and
  grows to fit, instead of naming only one (the user, q44: panels "listing what changed in each update").
  `updates_waiting` carries the titles. The titles come from `stage_note`, one per change landed.

### Takes on the beat: sync, loop and warp as stage ops

- `stage_take_sync` measures how takes keep time with a song (pulse, own BPM, the rate onto the beat, the first
  low point, the seam) and with each other (lag in beats, movement match); `loops=True` lists the best whole-bar
  windows of a long take. `stage_take_loop` cuts a window (given, or the best at a bpm) into a silent loop whose
  last 0.4 s cross-fade into the frames before it. `stage_take_warp` makes a loop of exactly N bars with its hits
  moved onto the beats. Ported from the film session's sync_measure.py, loop_cut.py and beat_warp.py with the
  parameters tuned with the user (ledger:M145); on Nate's takes the port gives the same numbers, windows and
  frames as the scripts. New takes drop the performance link (no borrowed voice), the trim and the label.

### Takes play smoothly between their samples

- Take playback poses the frame between the two recorded samples around each render frame (positions lerp,
  quaternions nlerp the short way), instead of holding each sample until the next. A take recorded at 19.8 Hz
  (Nate's dance, 20261005_122454) played at 72 fps held the right wrist still on 73% of frames, then jumped
  (95th percentile 46 mm); mixed, 5% still and 21 mm. Samples more than 0.25 s apart (tracking lost) are held.
  Only what posing reads is mixed (head, hands), into a reused buffer: 32 microseconds a person a frame on the desktop.

### Load sets: unload a group of the room, with a placeholder

- `stage_set_define` names a group of the room (node names or globs, and people), `stage_set_load` unloads it or
  loads it again, and `stage_sets` lists the sets and what they match. Saved in world.json (`sets`, `unloaded`), so
  an unloaded set stays out across reloads. An unloaded set is hidden before the room is staged (no shader compile,
  texture upload, draw or shadow), what only it used is freed on the GPU, its people neither play, follow nor rest,
  and a ghost box per member with one label carrying the set's name stands in its place (two draw calls). Loading
  brings it back a piece at a time. The page state carries `sets`.
- A scene switch reads the new scene's world.json before its room is staged, not after (resting people and load sets
  read it).

### People rest in their start pose

- A person with a start pose (`stage_actor_start`) stands in it whenever nothing plays on them, at load and after
  every stop: their own body, posed once, with the statue baked into the scene hidden. `idle=False` keeps the statue.
  Setting or clearing a start pose re-poses the resting person on an open page at once (page command `actor_rest`).
  The six dancers had stood with their arms out since they were imported ("a pose to leave everything in").
- The pose read-back of a take-frame start pose (with nothing playing) no longer fails.

### The first sketches: mastered, balanced, the first one first, said plainly (ledger:M148, M150, M151 S-1..S-5)

- S-1 (M148): every sketch goes through a master stage for its style (a low cut, glue, mono low end, a limiter at
  -1 dB, the loudness its style wants: four-on-the-floor about -9.5 LUFS, classical about -17) and a hall bus; keys
  get a little warmth. Before, a sketch was the dry mix at about -16 LUFS ("is this mastered?").
- M150: a stems render weighs the tune against the other parts and moves its fader so it sits 4-6 LU over them
  (it sat +1 to +13 LU with fixed faders).
- S-2: the first sketch comes back as soon as it is rendered; the others render in a background process
  (`sketch_wait(project)` says when they land). background=False waits for all.
- S-3: the readings differ for real: within what the words fixed, the sparser one moves key and the busier one gets
  an intro and an ending (so another length). The reply says how each differs from A.
- S-4: the reply opens with SAY TO THE PERSON, in a musician's words: what each sketch is, what was swapped
  ("you asked for a flute; that isn't here yet, so a violin plays the tune"), words it can't make yet ("'gritty'"),
  with no voice IDs. The engine detail moves to FOR YOU lines.
- S-5: a voice that is not in the curated showcase never plays a first sketch; its part is left out and said.

### The blind crop check: a pre-exam gate by eye (ledger:M158, Voice's method, Nate's rotation idea)

- `exam_eye_crops(pairs, out, windows=)` cuts the same window from the real and the made clip of each pair as
  spectrograms, side by side in a random order, with the key hidden; `exam_eye_score(out, answers)` scores the
  agent's picks and says NOT READY when they beat chance (p < 0.05). blind-tests.md: the check, zooming two ways,
  and the reveal after the answer. 211 tools.

### A pause tap stops the set by itself (the Live DJ's HANDOFF 50, Nate 10-06 15:08)

- The phone server fades the playing engine out over 4 s and stops it when the person taps Pause or says "pause the
  set", then captions it and posts `{'kind': 'control', 'what': 'paused'}`. Before, the set played on until an agent
  read the tap (70 s on 10-06).

### phone_unsay: take captions back off the phone page (Nate 10-06 15:03)

- `phone_unsay(match=, since=, n=)` removes captions from the page, its history and its pinned line, and closes the
  matching notification. phone.md: nothing personal on the page (it may be on a screen recording). 209 tools.

### The phone page measures its gaps (Nate 10-06 14:56: "a profiler")

- Every stall the browser reports and every freeze (the stream not advancing for over 1.5 s) is logged as a `stall`
  page event with its length, whether a voice note was recording, the playback rate, the network and which mic was
  open; audio route changes log as `route`, and `note_start` says which mic and how long it took to open. They show
  in `phone_timeline` and `phone_listen(page=True)`.

### The phone page's background: layers, looks on the bar, scenes (ledger:M160 phase 1, Nate 10-06)

- `phone_vibe(layers=[...])`: up to three effects at once, each with speed, density, size, angle, opacity, colours
  and a blend mode; `hue_drift` turns the colours over time. New looks crossfade in.
- `phone_vibe(at='bar:N', ramp_beats=)`: a look lands on the bar the phone hears (the page applies it on its own
  stream clock; the server folds it into the standing vibe 8 bars later), and repeated calls build a list of moves.
- `save=` and `scene=` keep and recall named looks. The state carries `room` (the engine's bar) for a page that is
  not on the stream.

### phone_sounds: the page plays sounds an agent made, one per event (Nate, 10-06)

- `phone_sounds(event, path, gain_db)` attaches a short sound (wav, ogg or mp3, at most 5 s and 1 MB) to an event:
  message, note_start, note_end, note_sent, error, tap, love, change, mood, offer, panel, chapter. The page plays it
  on that event only; events without one are silent, and the four note tones fall back to the built-in ones. The
  sounds persist across restarts. phone.md: make them with ismail, then attach them. 208 tools.

### The phone page opens the mic only for a note, so earbuds keep music quality (Nate, 10-06)

- With the mic open the whole time the page played, Bluetooth earbuds (Nate's Dime 3) stayed in call mode and the
  set sounded bad. The mic now opens when a note starts and every track stops when it ends. "Mic: kept open" brings
  back the old way (earbud notes with the screen off, in call quality); "Record: phone mic" records with the
  phone's own microphone.

### The live engine renders lazily and says STARVING before the underruns (ledger:M156, hq:D-30)

- A clip queued more than 32 bars ahead renders nothing until it comes within 32 bars (PRELOAD_WITHIN_BARS). On
  10-06 a 21-minute piano set queued in one call rendered every clip's first pass at once: a backlog of 10,089
  renders and 82 underruns in 6 bars. Renders already went out earliest-needed first.
- live_status says STARVING (the bar, the track, the seconds of render work before it against the seconds until it
  sounds) when the line, earliest-needed first at the measured rates, cannot keep up.

### Docs: where the machine board lives; 207 tools (S54, hq:D-30)

- setup.md and development.md say the board follows `ISMAIL_SONGS` (`<ISMAIL_SONGS>/_machine`, or
  `ISMAIL_MACHINE_DIR`): right for a stranger, a trap for a test on a shared machine (first-run dress rehearsal 2).
- The README counts 207 MCP tools (82 `stage_*`).

### The governor holds a run to its threads and pauses a job far past its memory (ledger:M153, M154, hq:D-30)

- `machine run` pins its command to as many cores as its threads (the highest cores no other job holds; children
  inherit it, and the meter sets it again on a child that changed it), and the thread variables now include Numba's
  and Rayon's. A `run --cpu` at 2 threads used about 5 cores, because CTranslate2 ignores the BLAS variables.
- A job at 3 times its `--mem` while less than 10 GB of commit is free (or 15% of the commit limit, if smaller) has its processes paused, never killed: the
  board says SUSPENDED: OVER and how to go on, `python -m ismail.machine resume <job id, pid or name>`, after which
  it is not paused again. 10-06: an ffmpeg declared 3 GB and took 37 GB during a live set.
- The board reads a job's file again when its meter is replacing it (Windows), so a running job never drops off
  the board for a moment.
- A docstring's `D:\ismail` made an invalid escape warning; it reads D:/ismail now.

### render stands in line when the machine is busy (ledger:M152, hq:D-30)

- A plain `render` refused on WAIT and told a newcomer's agent "force=True only if the user says so" (first-run dress
  rehearsal 2). It now waits in line up to `wait='10m'` (the default), says how long it waited, and `wait='0'`
  refuses at once as before.

### phone_route survives a restart (ledger:M157, hq:D-30)

- The route is kept in the phone's state.json and loaded again when the server starts (when its folder still
  exists), so a restart no longer sends the person's notes back to the playing engine's project.

### Exams know the devices; exam_check hears a band-limited take and lopsided sides (ledger:M146, Nate's approval 10-06)

- vox:r39 was wasted: the takes went through Bluetooth earbuds' microphone (16 kHz audio, nothing above 7 kHz), the
  synth filled that band 45-50 dB above them, and exam_check said READY. It now fails classes whose 1/3-octave levels
  above 6 kHz differ by more than 10 dB and twice the spread within a class, warns on any clip with nothing above
  8 kHz, and warns when a class comes first in 80 % or more of 6 or more trials (all of them, as in vox:r41's first build,
  already fails).
- `phone_exam` asks what they listened on (earbuds, headphones, phone speaker, speaker; remembered) and sends it with
  the answers, with the output and microphone the browser can name. blind-tests.md: record the devices every round.

### A priority says when it matches no job; `--for 3d` (ledger:M143, Nate's approval 10-06)

- `who` defaults to the working folder's name, so nearly every job from the repo is `ismail` and a priority given to
  a session matched nothing unless that session set `ISMAIL_SESSION`. Setting or reading a priority, and the board,
  now warn when no job of that name ran or waited in 48 h, and list the names in use. SKILL.md tells every session to
  set `ISMAIL_SESSION`.
- Durations take days (`priority voice --for 3d`), and a grant beyond today shows its day.

### Takes on the music clock

- The page keeps the song's time as the person hears it (the audio context's clock, less the output latency):
  `stage_music_time`, `music` in the page state, and `music_start` / `music_stop` events. `stage_music(start=)` begins
  a song partway in.
- `stage_actor_play(at_music=<song seconds>)` plays a take on that clock: its first frame sits at that song second and
  every frame reads the song's time, so a beat-warped loop stays on the beat however late it started and wherever the
  song loops. The film's dancers were warped onto the beat, but a take started on a command landed 0.3 to 6 s late.
  Side server: the take followed the song within one take frame (median 16 ms, worst 41 ms at 30 Hz frames).

### The phone's Listen and Talk keys show their state with an icon (Nate, 10-06 09:59)

- Listen: play, stop while playing, a breathing arrow while it reconnects or buffers, Resume when the phone wants a
  tap. Hold to talk: a mic, the mic with sound while talking, an arrow while the note uploads, a check when it
  arrived, a struck mic when the mic is blocked or the note waits to send. A waiting icon breathes, and stops for
  reduced motion.

### Every mp3 carries ismail; downloads have names; the piece's position stays on screen (Nate, 10-06)

- `ismail/tags.py`: ID3 tags on every mp3 ismail writes (title, artist, album, date, encoder, the GitHub link as a URL
  frame and in the comment: "Made with ismail (https://github.com/newsbubbles/ismail)"). Nate: "very important for
  provenance whenever we're shipping out MP3s". `render(mp3=)` tags with the song's name; `phone_offer` sends a
  tagged copy with a readable name from its label (their own file is never changed). mutagen is a dependency.
- The Download key no longer runs off the right edge of a phone.
- `phone_now(length=, sections=, into=)`: the page keeps a position line on screen (elapsed / length, the section,
  the next one), in time or bars. Nate: "something that's always on screen ... that shows where we are in the song".
- When the playing piece began survives a server restart (ledger:M139: lines after a restart said 0:00 into it).

### The frame beat says where the head was

- The page's 5 s beat (clientlog.jsonl) carries `head` (position in Blender metres, yaw and pitch in degrees, yaw 0
  along +Y) and `worstAt`: the head and the triangles and draw calls of the worst frame in those 5 s, so a stall is
  read against the view it happened in (the frame budget: under 400k triangles and 300 draw calls per view). It also
  carries `shadowFrames` (frames in the window that re-drew the shadow maps), `playing` (people playing or following)
  and `eyes` (2 in the headset: triangles and calls count both eyes).

### Takes keep what was said while recording

- A take's audio is transcribed as it lands (`takes/<id>/voice.json`, a `take_voice` event), with word times on the
  take's clock, snapped onto the measured voice. A take kept from a Follow, or recorded during one, takes its words
  from the performance's voice clips that fall inside it.
- `stage_takes(scene, query, person, kept)`: takes newest first with what was said, label and notes; `query` finds
  takes by their words, label or notes, and shows each hit with its second. `stage_take_note(scene, take, label,
  note, at)` names a take or adds a note; `stage_take_transcribe(scene, take)` fills in an older take's words. The
  person asked for it: he says a take's name and notes while recording, and records many takes of one thing.

### The headset speaks when the server's voice is off

- While speakwright is off, `stage_say` was silent in VR (the server answers 502). The page now says the line with the
  headset browser's own speech engine instead (Web Speech), still waiting while the person talks; the event says
  `voice_spoken` with `via: "headset speech (server voice off)"`, or `voice_error` when the browser has no engine or
  voice. Lines in the speech cache still play from the server.

### The phone page in the README and the skills (Nate, 10-06: "this phone app feature should start gaining prominence")

- README: the tagline names it, and a section "The phone page: the set in your pocket" says what it does (listen
  with the screen off, earbud voice notes, taps and history, exams, the vibe, bars or time, install, the timeline).
  203 tools (17 `phone_*`).
- Skills: SKILL.md (away from the desk all day; answer in time to a casual listener), user-experience.md (bring the
  page to where they are; a new "Bars or time" section), blind-tests.md (exams on the phone), live.md (the page is
  part of the show: marks and vibe on every chapter change, the timeline), setup.md (optional: the phone page and
  Tailscale).

### The phone server restarts without dropping the person; a stream that fell behind catches up

- A restart after a merge dropped Nate's stream mid-set (10-06 08:38, "Why'd you stop?"), and a page whose command
  count was past the new server's missed every command after it. `phone_restart()` tells the page first; the page
  sees the server's boot change, starts its commands from 0, reconnects the stream at the live edge, and loads new
  page code (`build`) when it is next on screen and idle.
- The phone's player pauses on a weak network and carries on from there, so the delay grew (17 s, then 40 s). Past
  15 s behind it now plays 8 % faster with the pitch kept until it is within 6 s; the Live key shows the delay.

### The phone page takes the music's vibe, and reads in time as well as bars (Nate, 10-06)

- `phone_vibe`: an agent sets the page to fit the song (Nate: "change the colors ... how the headers look ... song
  covers in the background, blurred ... JavaScript effects ... I feel like you're there"). Presets (rain, calm,
  warm, night, peak), colours, a heading face from nine, an art layer with blur and dim, one ambient effect (rain,
  particles, pulse on the set's beat, grain, aurora) that stops while the page is hidden or for reduced motion.
  The server holds every vibe to a dark ground, ink 7:1 and accent 3:1, and refuses others saying what to change.
- Bars or time (Nate: "an option for the person to understand music in time"): lines from the page carry
  `into_s`, the page has a Bars/Time switch ("2:31 in" with the clock), and `phone_timeline` shows both.

### The phone session is a take: the page reports what happens on it, on one clock (Nate, 10-06)

- Nate: "can you see when I download stuff? where I'm scrolling ... like a VR take ... but for the mobile interface
  ... do you know if I'm on my phone?" The page now sends its own actions, timed (`/api/events`, kind 'page'):
  open with the device, Listen and Stop, hidden and visible, the section in view, downloads, clips, panels, note
  start and end, earbud presses. Every line from the page carries `room` (the engine's bar, also off the stream)
  and `now` (the piece); voice notes carry `dur_s` and `ended_by`.
- `phone_timeline(minutes=)` lays the session on one clock with what they said. `phone_listen(page=True)`
  includes the page's actions; without it they never wake a waiting agent.
- An open panel or exam, and the files it offers, survive a server restart (a merge dropped an unread one).

### The phone page: momentary keys and a tap history, notes that never cut off, installable (Nate, 10-06)

- Nate: a key "stays pressed even once that has been sent ... when I press it again, I'm unpressing the button".
  Every key is momentary now (Love this no longer rests in the accent, the mood detents flash SENT instead of
  staying selected), and the page lists what he asked for: each tap and mood with its time and the piece that
  played, today's count, and the mood he asked for the next chapter. The server keeps it (it survives a reload or
  a restart).
- Now and next carry a small mark: a heart for a piece he loved, a loop for one played again, NEW for one just made.
  `phone_now(now_mark=, next_mark=)` sets it; without it a love tap during that piece shows the heart.
- "The voice recording should not cut me off": notes stopped at 60 s mid-sentence. They now run until his press,
  30 s of quiet, or 10 minutes (a warning 20 s before).
- Installable from Chrome, after his tooler PWA: PNG icons (192, 512, maskable; `python -m ismail.phone.make_icons`
  redraws them from icon.svg), an Install app button when Chrome offers it, and optional notifications for what
  the DJ says while the app is in the background.

### The phone page picks the set back up after a reload, and a spoken answer reaches an open page

- On Nate's walk (2026-10-06) the page reloaded (the phone dropped it while locked, or it was reopened): the stream
  stayed off, his voice notes still came in, and a spoken `phone_say` was dropped ("nobody is listening"). The page
  now remembers it was listening (30 minutes) and reconnects by itself; when the phone wants a tap first it shows
  Resume, buzzes and says the set is still playing, instead of retrying silently.
- `phone_say(speak=True)` with nobody on the stream sends the words to the open page as a spoken clip; with no page
  open it says so. `phone_status` says whether a page is open and whether it is on the stream.

### A script under `machine run` can render; a slot caps its threads (ledger:M81, ledger:M132, hq:D-16)

- `machine run` hands its slot to the command it starts (`$ISMAIL_SLOT`): a script that renders or measures runs
  those ops in the run's slot instead of being refused, or deadlocking, on a second one. That happened three times
  on 10-05/06 (a paper harness, the DJ's salsa checks, the phone cue tones). A child's GPU step still takes the GPU
  slot; a live engine always takes its own.
- A slot counts jobs, not cores, so it now caps its threads everywhere: BLAS and OpenMP in its process, torch where
  ismail loads it (the perceptual model, demucs), and a command's environment under `run` (`--threads`, 2 by default
  for `--cpu`). One slot that ran a render, eq_match and the perceptual model on every core took the CPU to
  88-100 % and a live set dropped 359 buffers.
- While a set is on air, the CPU jobs beside it share half the machine's threads; a job that would pass that waits
  and says how many it may ask for. The board shows each job's threads.

### The skill says when to offer the phone page, and how it is hosted

- `references/phone.md`: when to offer it (a set plays and the person steps away: a walk, bed, another room), the
  one line to say, and the hosting (Tailscale today, with what the person installs; the home network later, which
  needs https for the microphone). SKILL.md's which-tool table and `live.md` point there. Nate asked that the skill
  know the page exists, not only the code.
- The earbud cue tones (a note starts, ends, arrived, blocked) are made with ismail on its measured grand piano
  (`songs/_phone_cues/make_cues.py`), set to -22 LUFS so they sit under the music. The page keeps its synthesized
  tones as a fallback until the files load.

### The phone page's keys get icons (Nate: "they could have their own little SVG icons too")

- Love this, change it up, calmer, more energy, quieter, louder, pause and resume set each have an icon drawn to
  the page's contract (24-unit grid, 2px strokes, square ends), with the word small beneath it. A tap lights the key
  and reads SENT for a moment, so it can be read at a glance with the phone half out of the pocket.

### The pose read-back says when a limb fell short

- `stage_actor_pose` replies `short`: the limb ends that could not reach their target in that pose, in metres
  (`{"hand_l": 0.066}`: the left hand stopped 6.6 cm short of where it was sent, at full reach). A hand that cannot
  reach the bar from a start pose is a number for the agent, not something found in the headset.

### A brief for a take helper agent

- `references/stage-takes.md`: when the person wants a fast take loop and the scene's agent is busy, a helper agent
  runs it from a brief (scene, who, goal, setup, takes, the stage tools it may use, trial moves only, when to stop,
  what to hand back). The person asked for it so the loop never waits on a busy agent.

### A set on air comes first, for now (ledger:M127, hq:D-11)

- While a live engine is on the board, the governor holds GPU jobs and Blender renders (also those registered as
  `--cpu`: blender, eevee, cycles) until the set ends: two real dropouts in a set tonight came from renders that
  started beside it. Audio renders and other CPU jobs still run. The board shows ON AIR and the policy.
- It is a policy, not a hard rule: `python -m ismail.machine on-air --policy set_first|off --by '<who>'`. Nate's
  words: "only for now during sets"; revisit after the laptop's repaste or a faster engine. `render_first` (the
  render runs when it is a burden and the DJ pauses the set, announcing it) is named as the future direction and
  refused until it is built.

### The phone page from the earbuds (Nate: "control everything through voice ... without taking the phone out")

- While a set plays, the earbud's press takes a voice note and the next press sends it, with tones you hear in your
  pocket (start, end, arrived, blocked). Short notes that are only a command act as one: "stop listening" stops the
  stream, "love this", "change it up", "calmer" and the rest arrive as taps with `via: 'voice'`. The microphone is
  held from Listen so a press works with the screen off. A note stops itself after 60 s.
- When the server does not answer, the page backs off to one try every 30 s instead of every 4 s.

### The phone page, designed (Nate: "check the style" with the taste skill)

- A deliberate look instead of the dark default: a field radio for one hand at night. The bar you hear is a tape
  counter, recording is an ON AIR lamp, the keys are labelled hardware keys (Archivo condensed, JetBrains Mono for the
  counter and the log, one accent derived from the name), and nothing is a symbol glyph Android could draw as emoji.
- The talk key shows your microphone's level while it records, so you can see it hears you. The DJ log has a time
  column. The mood control is a four-step selector, and the DJ's own buttons sit in their own section.

### A mirrored take plays back mirrored; the gallery stays put

- A take made while the Follow mirrors keeps `mirror` in its meta (at the start, on every change while it records, and
  when the last Follow is kept), and plays back mirrored, as does the playback right after the Follow.
  `stage_actor_play(mirror=)` overrides. The user, 2026-10-05: a take recorded with Follow's mirror played back the other
  way.
- The gallery's back and next change the picture on one panel where it is. Each answer used to fade the panel out
  while a new one opened 30 cm aside ("closes the whole gallery and opens it again"). Panels gain `stay` and
  `rewrite(id, {title, text, image})` for this.
- A start pose of 'rest' on a take played in place (or before the stand-in has loaded) stands at the take's anchor
  instead of failing.

### Loading without the long stalls (the "parallelograms" on the Quest)

- The Quest has no parallel shader compile, so the room's one precompile (`compileAsync`) stalled a frame for about a
  second, and the trees' growth shaders, patched after they were staged, all compiled in one drawn frame (1.6 s, 28
  programs). A stalled XR frame is reprojected: the user saw "vertical parallelograms on each eye".
- Now, without the extension, `reveal.js` compiles one material per frame before the reveal (the root compiled with the
  other meshes' materials set aside for the call), and waits for each program's link inside that frame, not at its first
  draw. With the extension it compiles at once off the frame, as before. `?paced=1` tests the Quest's path on a desktop.
- A tree's growth shader is patched before it is staged (`patchGrowth(root)`), so it is compiled with the rest.

### Pinch again to reach what is behind or inside

- In VR, a second pinch at the same point (within about 6 cm, or 4% of the ray's length, and 6 s) takes the next
  thing under it, then the next, round again: along the ray up to the first wall or locked part, and for a finger,
  every box the fingertip is in, smallest first. It works for selecting and for moving. Each pick among several emits
  `pick_cycle {how, item, n, of}`. A person's box covered the cup in their hand and the things near them, so a pinch
  there always gave the person.

### A performance always has a way out, and agents hear it while it runs

- The user spent seven minutes inside a performance an agent had started ("I'm basically just stuck in a
  performance"): its Follow had no panel, its voice was one clip transcribed only at the end, and nothing he said
  reached the agents. Now:
  - an agent's Follow (`stage_actor_follow`) opens the Follow panel too (Stop, Mic off);
  - `stage_perform(action="stop")` ends the whole performance: the Follow, and a take recording with it;
  - the person can always end it: both thumbs down held 1.5 s (the HUD says so), or saying "stop the performance"
    (or "stop the recording", "end performance"), which the server turns into the same stop;
  - a clip cuts itself at the first pause after 6 s, or at 25 s, with no gap (the next starts before it stops), so
    its words arrive while the performance goes on; a clip with no voice in it is not sent to the speech server;
  - a clip's words (`perform_clip` with text) reach `stage_listen`, the text first.
- A take made in a performance plays its recorded voice only on the person it was recorded for:
  `stage_actor_play(voice=)` True plays it on another body too, False keeps it silent. One clip sounds once, however
  many bodies play the take and however often it is played (a dance take on six dancers, played twice, built up "a din
  of just me").

### Follow counts down first

- The menu's Follow (and Take on a person) counts 3, 2, 1, GO in front of the user, with ticks, before the person
  starts following: the person holds their pose while the user takes it, and the Follow (and its performance clock)
  starts from the user's pose at GO. `stage_actor_follow(countdown=)` does the same for an agent. The user's take
  started from his pose when he pressed Follow, hands down while the man's were up ("kind of makes it impossible").
- A played take honours the pins: the ones it was made with (kept in its meta as `pins`, Blender xyz), else the
  session's. Sam's take played anchored by the feet though his hips were pinned to the stool.
- A recorded voice plays back at a conversational level (about -30 dBFS RMS), not the raw mic level ("super loud").

### Start poses (the person starts from their own pose, the user's motion as changes)

- `stage_actor_start(scene, person, pose, mode)` keeps a start pose in the actor's profile (`actors/<body>.json`
  "start"): `'rest'`, a frame of a take (`{"take": id, "frame": n}`), or a pose exported from Blender
  (`{"bones": {name: {"rest": {head, tail, x}, "pose": {head, tail, x}}}}`, Blender metres, rest in armature space,
  pose in world). With `mode="relative"` (the default) every Follow and every playback starts from it: at GO the
  person holds the start pose, the head and spine turn as the user's head turns, the hands move as the wrists move
  (scaled to the body) and turn as they turn, the hips and legs keep the pose. `mode="snap"` keeps the old behaviour.
  The user held the bar while Sam's hands were up; the film assistant asked for start poses as data.
- `stage_actor_pose(scene, person, t)` reads the joints back in Blender metres (pelvis, spine, head, elbows, hands,
  knees, feet): now while following, at `t` of a playing take, otherwise the start pose (frame 0 of the next
  Follow), so contact can be checked by number.

### exam_check: every exam's pre-flight (D-7: self-checks live in tools)

- `exam_check(page or clips, key, secrets, submit_url, answers_path)` before any exam reaches the person: every
  clip exists and decodes; loudness within 1 LU; no blind leak (the key's classes or secrets in file names, URLs,
  metadata tags or the page source and what it loads, a key file the page loads, formats, lengths, leading silence
  or order that separate the classes); a marked test answer posted to Submit lands where the agent reads answers.
  READY or NOT READY, each problem named with what to do.
- `phone_exam` runs it and refuses a NOT READY exam (`key=`, `secrets=` for the leak checks; `check=False` only when
  the person asks to see it anyway). `references/blind-tests.md` section 0 says when to run it.

### The phone page: a live set in your pocket

- `phone_start` serves a page over the tailnet that plays the live engine's master as an mp3 stream, which keeps
  going with the phone's screen off. The lock screen and earbuds work too: next = change it up, previous = love
  this. It has 30 s rewind and jump to live, reconnects by itself, keeps taps made offline, and has 64 or 128 kbps.
- Talking back: hold to talk, or tap once to talk hands-free. Notes are transcribed on this machine, waiting while
  the CPU is over the governor's limit. Taps on the page: love, change it up, calmer or more energy, quieter or
  louder, pause or resume the set, start a set. A mood hint: calm, steady, lift, peak. Every line is stamped with
  the bar the person actually heard and how far behind the room they are, and lands in an inbox the agent reads
  (`phone_listen`), in the song's `notes/phone_inbox.jsonl`, and in hooks.
- Agents drive the page: `phone_now` (now playing, next up, why recording is on or off), `phone_say` (a caption, a
  pinned "since you left", or a line spoken into the stream when answering), `phone_ask`, `phone_panel_show`,
  `phone_exam` (blind exams with Submit, answers written to the exam's file), `phone_offer` (downloads),
  `phone_buttons` (their own buttons as data), `phone_buzz`, `phone_status`. Reference: `references/phone.md`.

### voices_list names each voice's function (from the e002 pilots)

- `voices_list` shows `fn=perform` or `fn=voice` on every code voice; it used to say `'fn': 'voice'` for all of
  them, and electric, emily, kit70, rusty, rhodes and crackle only have `perform`, so an agent's first `track_add`
  failed. A track that names `fn: 'voice'` on a performer voice now plays it with `perform`, as leaving `fn` out
  already did.

### Setup a novice can sit through (M119, the dress rehearsal)

- `references/setup.md`: say the install takes about five minutes and looks frozen; prefer `uv pip install`
  (faster, shows progress); warm up the plugin's uvx command before the plugin lines (a first start that builds
  for minutes can time out); the app restart said once beforehand and done once; what the Allow prompts look like;
  winget lines runnable as written; reloading PATH inside a desktop app; the play command per OS; and
  `ISMAIL_SONGS` set to their music home so `guide`'s first-session check and the machine board see their songs.
- Whether they play or read music rides on the first session's first question: two questions, not three
  (`AGENTS.md`, `guide`).
- `machine`'s WAIT text no longer tells an agent to ask someone new to close things: wait quietly and say "the
  computer is busy, one moment" (a person who runs other work on the machine can still be asked).

### sketch hears a gentle classical or church brief (M118, the organist's dress rehearsal)

- A classical feel: "prelude", "hymn", "chorale", "church", "sacred", "baroque", "classical", "chamber", "adagio" and
  the like give a slow piece (56 to 72 BPM) of piano broken chords, a piano tune, cello and contrabass, in two
  phrases and a fading close; its chords move by function and every reading closes on a cadence (V I, or IV I for
  the second), so the chord readout ends on the tonic.
- "Gentle", "soft", "quiet", "calm", "slow" and the like play every part lighter and keep the tempo at 76 or under.
- "Strings" is a section (violin and cello, and contrabass when no other bass plays), and the reply says so; parts
  a classical brief fills in are named in the reply.
- In a classical or soft sketch the tune sits on top: every other part is at least 9 dB under it (the rehearsal's
  cello read 17 to 22 dB over the piano melody; now each part measures 4 to 6 LUFS under it).
- The readings sit in order inside the tempo range: sparser the slowest, busier the fastest. The reply names the
  closing cadence and a soft reading.

### Point an agent at the repository and it knows what to do (family first sessions)

- `AGENTS.md` (and `CLAUDE.md`, which loads it): a person says "set me up with ismail" and a link; the agent reads
  the playbook, sets it up itself, calls `guide` (the first session for someone new), learns whether the person
  plays or reads music, and knows where contributing and changing ismail are described.
- `references/setup.md`: setup done by the agent: look before installing, the package-manager commands per OS, the
  one-line warning before any prompt only the person can answer, the plugin / MCP / pip routes, proof by a sound
  they hear, and errors that are the agent's to solve (and to write down for the maintainer).
- `references/contributing.md`: contributing for someone who has never used git: in the studio (review, ask,
  HANDOFF.md, one sentence to start a new conversation), through GitHub (signup, `gh auth login --web` with the
  code read to them, fork and pull request done for them, their no-reply address) or without it (a bundle).
- README's agent steps start from AGENTS.md and set-up-yourself; the first questions are `guide`'s.

### sketch honours the brief's words (M021 run 1b)

- Hats play in the groove (16ths when crisp); a named ride rides the groove's second phrase and is the whole
  breakdown (no snare, no fill out of it); "fades out", "fade-out" and "fading out" make an outro that fades.
- "sparser" is sparser (the tune's passing notes out) and every chord loop starts on the tonic (i iv VII III read as
  G major); the reply says when no instrument was named for the melody, that the sketches are their own projects,
  and not to polish one before the person picks. Each sketch states its sounds (track_model), so renders stop
  flagging the sub as unstated.
- `ismail.machine`: the CPU reading is shared between processes on the board for 10 s; each CLI call used to
  sample the CPU for 2 s on its own (7 to 9 s of queue wait per heavy op on an empty board).

### Sampled voices: a Rhodes, a real kit, a clean guitar, vinyl crackle (from udio_ab_01)

- `rhodes` (keys): jRhodes3d by Jeff Learman, a 1977 Rhodes Mark I DI, 5 velocity layers. `rusty` (drums): Big
  Rusty Drums by Karoryfer, velocity layers, round robins and mics; a missing piece is skipped, not a crash.
  `emily` (guitar): Emilyguitar by Karoryfer, a clean DI guitar with legato, bends and vibrato lanes. `crackle`
  (fx): designed vinyl surface noise. Moved from the udio_ab_01 song, which made the same trip-hop brief.
- The samples stay out of the repo (`ismail/samples.py`): `samples_list` shows each set, its size and licence;
  `samples_fetch(name)` downloads it into `~/.ismail/samples` ($ISMAIL_SAMPLES) on the person's yes, or registers a
  folder that already holds it. A voice whose set is missing says what to fetch. Licences: Big Rusty and
  Emilyguitar are CC0; jRhodes3d samples are CC BY-NC 4.0 (the author grants CC0 for music made with them), so
  ismail ships the voice's code only.
- `sketch` reaches for them: a Rhodes, a breakbeat or live kit, a clean or bluesy guitar, vinyl or dust in a brief.
  When a set is not on the machine it plays the stand-in (grand_piano, kit70, the fitted strat) and says what to
  fetch, how big, under what licence. showcase.json lists them with their fx chains. 183 tools.

### `sketch` reads the brief (M110, from the first cold-start run)

- A brief's tempo, key, genre (trip-hop, hip-hop, house, jazz, rock, ambient ...), instruments and form (intro,
  groove, breakdown, build, return, fade) now shape the sketches: drum feel (break, four on the floor, jazz, rock),
  ride and crisp hats, seventh chords, a pentatonic melody for blues words, 4-bar sections with the right parts
  in each (a breakdown is keys and ride, an intro has no kick, an outro fades). Three readings: as asked, sparser,
  busier. A brief that names nothing still gets the three styles.
- What has no voice yet is said first ("SAY TO THE PERSON: asked for Rhodes: no electric piano voice yet:
  grand_piano plays its part"; vocals, winds, pads likewise).
- `sketch(..., base='<letter>')`: the next round is that sketch changed by the person's words (slower, no guitar,
  add a pad, D minor); each sketch keeps its spec in `sketch.json`. `n` sets how many.
- Each sketch prints a line when it is ready and reports its loudness and peak.
- showcase.json: a clean single-note guitar (the fitted strat through the clean rig) and `sub_bass` (a sine: the one
  synth a sketch uses); the gaps now list an electric piano, a singing voice, winds and brass.
- `ismail.machine`: a running job's memory counts what it added, not the holder process it runs in, and OVER needs
  0.5 GB past the declaration (every in-process render was flagged).

### A new person's first session: sketches in minutes (M110, S36)

- `guide` opens with a first-session block for a person who has made nothing with ismail yet (no finished render
  in the songs folder or beside the project, and no `~/.ismail/first_session_done`): two sentences on what this
  is, at most two questions, sound within about five minutes, short rounds, one "change just one thing" edit.
- `sketch(project, brief)`: two or three short contrasting sketches (solo piano, string trio and piano, a small
  band), about 30 s each, on measured voices only, rendered to mp3 in one call (about two minutes for three).
  Each sketch is a normal project: a motif and its answer in 4-bar phrases that vary and come home, chords that
  move, parts in their own registers and rhythms, levels trimmed to -16 LUFS. Key from the brief's mood or `key=`,
  chords from `progression=`.
- `sketch_keep(project, letter)`: the pick becomes the song (lineage to the sketch, the brief as its objective),
  and the first session is marked done.
- `ismail/voices/showcase.json`: the voices a first sketch uses, with ranges and why each is trusted; `guide`
  shows the list and `voices_list` marks them `*`. Not covered yet: a real-sample acoustic kit, a pad voice.
- SKILL.md: "A person's first session", and step 0 says the kept sketch is the example. 181 tools.

### The governor guards the disk and the commit (M72)

- `ismail.machine`: no new heavy job while a drive jobs write to (the songs folder's, the working directory's, a
  render's project) has under 15 GB free, or while under 6 GB of commit is free. The refusal gives the numbers and
  what to do. A live set on air is never held. D: filled four times in five days: render caches, takes, a game
  unpack, and the Windows pagefile, which grows into the disk when commit runs out.
- `run --disk <GB>` and `slot(..., disk_gb=)` declare what a job writes, and the check counts it. `render` declares
  its track cache and wavs itself, and a refusal says `cache=False` skips the cache.
- A running job's memory (private bytes, what the commit counts) is on the board every 15 s. Past its `--mem` by
  25% it shows OVER there, and its own output says so once (2026-10-05: a Blender job declared 7 GB, took
  10.7 GB, and the pagefile took D: to 0.2 GB in nine minutes).
- The board's new disk line shows each drive's free space and the pagefile's size. `live_status` ends with the
  machine's free commit and disk.
- History: each job's peak memory, its bytes written and the drive's free space at start and end. `history` sums
  the writes per song and counts the jobs that went past their memory.
- `machine_disk` (op) and `python -m ismail.machine disk`: the biggest folders under songs/ with their growth since
  the last day's snapshot, and every `_reclaim` folder. Freeing space means moving a project's finished
  intermediates into its `_reclaim/`; nobody deletes, the user clears it. 179 tools.

### Provenance: the string profiles credit their real sources (M73)

- `voices/strings/{violin,contrabass}.mimic.json` were measured from VSCO 2 Community Edition (Versilian Studios,
  CC0) and `cello.mimic.json` from "real cello notes" by flcellogrl (Freesound pack 12408, CC BY 4.0), both via
  tonejs-instruments; their `source` fields said Philharmonia Orchestra, which tonejs-instruments' own source list
  does not support. instruments.md says the same, and that the cello needs credit.

### What ran, and what it used: the machine's job history (M87)

- Every job that held a slot leaves one line in `<board>/history.jsonl` when it ends (append only): what, the
  session, the song (from its working folder), the kind, the estimate, start and end, how long it waited in line,
  its exit (a `machine run` command's exit code, or the error that ended an op), CPU seconds of the holder and every
  process it started, peak memory, the whole GPU's busy seconds and peak memory while it held the slot (one
  `nvidia-smi` loop per job), and the machine's state at its start (GPU temperature, clock, throttle reasons, CPU).
- `python -m ismail.machine history [--song slug] [--since 7d|2026-10-04] [--jobs N]` sums it per song.
- History starts with this change; jobs before it were never kept.

### Keyed moves that glide; trial moves that never save (M107)

- `stage_key_interp(scene, object, mode)`: "smooth" makes a keyed object glide through its keys (a cubic Hermite
  curve per segment, tangents from the neighbouring keys; the turn slerps without easing) instead of stopping at each
  ("stop", what an object with no mode does). Saved in anim.json `interp`. The same curves are in
  `ismail/stage/page/interp.js` (the page) and `ismail/stage/interp.py` (`sample_anim`, for a render), held within a
  millimetre by a test (the stage and the Blender render were 74 cm apart between camera keys; the Crossroads
  render now reads `interp` too).
- `stage_object_set(trial=True)`: a test move that never reaches edits.json, which the build reads; where the thing
  was before is what saves, until it is edited for real (an agent's test camera move autosaved into the build).

### Live control maps and actor profiles (M100)

- An actor's profile lives beside its body: `scenes/<scene>/actors/<who>.json` next to `<who>.glb`, so it goes
  where the body goes. `stage_actor_profile(scene, person)` reads it: the rig type (read from the body's bones), its
  named parts (bone chains, parents first: head, spine, arm_l, leg_r, fingers_l, index_r, ... on people; any other
  rig gets one part per unbranched chain, so a tail is a part) and its saved control maps.
  `stage_actor_map_save(scene, person, name, pins, drives)` stores one; a map named "default" applies by itself
  when a Follow starts. Every control op names the scene's person; world.json actors says which body plays them.
- The control map: which part of the user drives which part of an actor, changeable at any moment, during a Follow
  too. The built-in human map stays the base; `stage_control_set(scene, person, part, mode, joint, at, scale,
  touch)` takes one part over: `hold` (keeps its pose, riding with the body), `effector` (arms and legs reach for a
  target that moves as a user joint moves, relative to where both were when it bound: the user's hands act out a
  seated man's feet; `touch=True` shows a ball in front of the user and binds when that hand reaches it), `pin` (reach
  for a point and stay), `mimic` (copy a joint's turn, spread down the chain or 1:1 per bone with a list of joints),
  `default`. A hand that drives another part lets go of its own arm and fingers. `stage_control_map` reads the map,
  applies a preset, a list of drives, or clears it. The Follow panel shows the drives.
- An anchor partitions control (each part its own source and solver), it does not cut the body.

### A Follow is a performance; stage_batch (S32)

- The Follow button starts a performance and the same button ends it. While someone follows the person, no gesture
  acts (no travel, menus, phone, thumbs or grabs; a poke still presses a panel), and the Follow panel says so.
- The mic records from the first moment of every Follow, in clips on the Follow's clock (seconds since it began),
  uploaded a second at a time so a crash keeps what came in: `<scene>/performances/<id>/clip_<n>.<ext>` and
  `perf.json` (person, markers, clips with their words). A stopped clip is transcribed with word times, snapped onto
  the measured voice (whisper starts a phrase's first word up to half a second early, in the silence before it),
  into a `perform_clip` event with the words on the Follow clock.
- `stage_perform(scene, action)`: `state`, `stop_clip` (read it while the person goes on), `start_clip`,
  `next_clip`, `mic_off`, `mark` with a label. The Follow panel has Mic off / Mic on. `stage_performance(scene, perf)`
  reads a performance as text: markers, clips, each word at its time.
- While the person performs, `stage_say` shows the line and does not speak it (it would be in the recording);
  `aloud=True` speaks it. `stage_ask` waits for after the Follow (thumbs are off).
- After a Follow it plays back on the person first, with the voice, while the card asks Keep / Discard. A take kept
  from a Follow, or recorded during one, plays its performance's voice with it wherever it plays (meta `performance`,
  `perf_shift`: where the take's time 0 falls on the Follow clock).
- `stage_batch(scene, ops)`: many stage ops in one call. The page commands go to the page as one command and run
  back to back; an op that runs on the server first sends the page commands before it. With `stop_on_error` the
  first failure stops it and the files changed on the server go back.
- speakwright (the speech server) answers `response_format=verbose_json` with word times; an older one still gives
  the text, and the event says the words are missing.

### The machine board's lock holds under a crowd (M85); one test file is not a heavy run (M86)

- `ismail.machine`: a job waiting in line no longer dies when the board's lock changes hands under it (a holder
  let go between the waiter's failed create and its look at the lock: FileNotFoundError killed a queued render with
  three sessions waiting). A dead holder's lock is taken by an atomic rename, so two waiters cannot both take it,
  and a holder removes only its own lock. Test: eight threads take turns 480 times with no error and no overlap.
- tests/conftest.py: a run is a heavy job (it waits for a CPU slot) when it spans 4 or more test files, or includes
  a file that renders whole windows (live parity, live song parity, round trips); one touched file of any size runs
  without a slot. Before, any run of 30 or more tests counted, so a single 31-test file was refused on a hot machine.

### Live tests: notes go straight to the stage dev, and updates come back (S31)

- stage.md and stage-dev.md: during a live test the studio agent in the room sends the person's stage notes directly
  to the stage dev (and writes them in the handoff); when a change touches the page's code, the stage dev tells that
  agent the update is ready, and the person takes it with the update gesture. The scene's content is the studio
  agent's to change; how the stage works is the stage dev's.

### Roles: studio agents, the maintainer, the stage dev (S24); the intake lists open pull requests (M77)

- `python -m ismail.handoffs` lists the open pull requests after the handoffs (title, branch, author, checks,
  whether it merges cleanly), marked new or updated since the last `--mark`, so a pull request is never seen only
  because its author sent word. It uses the GitHub command line when it is there and says so when it is not;
  `--no-prs` skips it.
- The skill names two kinds of agent: studio agents work within ismail (a song, a sound, a live or DJ set, a film,
  a scene on the stage), dev roles work on it. A studio agent that finds something general asks the person whether
  to contribute it and suggests starting a dev session; it never turns into a dev mid-conversation.
- New references for the dev roles, from the sessions that held them: `maintainer.md` and `stage-dev.md` (VR is
  one way into the stage, so its developer is the stage dev). development.md gains the area dev role, the start-up
  routine (progress file, machine, intake, report; every 4 hours by default, the person sets it), and why proactive
  and reactive work cost differently.
- stage.md gains the headset lessons it lacked: queue spoken lines and keep captions up until spoken, one sound one
  meaning, nothing that hitches while the person is in, a purpose for every run, panels that stay put, everything
  touchable by hand, who answers when two sessions listen, listeners reconnect after a restart; the reload advice is
  corrected (the plain scene address).
- development.md: a public voice, instrument or profile stays compatible with the songs that use it (new behavior
  as a new param whose default is the old sound); when it cannot, the new one ships beside its ancestor under a
  findable name, with `derived_from` in its data and credit both ways.
- blind-tests.md: the listening device is part of the exam (record it, one per round, a device sweep when it
  matters, calibrate the listener's floor with codec lenses, log the floor) (S30).
- Skill text describes actions in plain words with synonyms (send a message to a session, conversation or agent;
  a browser pane, preview or web view) instead of one harness's tool names.

### Faster mimic renders (M76); placement exams (S23)

- `mimic.render` computes each partial only while it sounds, so a long tail after the release costs next to
  nothing (a cello C2 with a 3 s tail: 2.5 s to 0.4 s, the same sound within -70 dB). Channels that would be equal
  (width 0, one player) are computed once.
- New mimic params: `mono=True` computes one channel for a caller that sums to mono; `floor=<dB>` skips partials
  that far under the strongest (off by default: 60 dulls the top of low notes by 3 to 5 dB).
- blind-tests.md: a placement exam needs a reference point and a clear task.

### The VR stage (M46)

- `ismail.stage`: the VR stage that grew in the Crossroads video (songs/crossroads/video/vr) is now part of the
  engine. A three.js WebXR page for a desktop browser or a headset (Quest 3), a server that serves a song's scenes
  (`python -m ismail.stage.server --scenes <song>/video/vr/scenes`), and the live link between the page and agents.
- 63 `stage_*` tools: `stage_start` / `stage_stop` / `stage_status`, `stage_events` (what the person did and said:
  voice notes, gestures, edits), `stage_world` (a scene's world.json), `stage_scene_export` (a scene rebuilt from
  its Blender build script in a heavy-job slot), `stage_note` (the "updates ready" card), `stage_cmd` for a command
  with no op yet, and 55 typed page commands (`stage_object_set`, `stage_say`, `stage_panel_show`,
  `stage_waypoint_set`, `stage_actor_play`, `stage_stream`, ...). Each one waits for the page's answer and raises
  with the next step when no page shows the scene or the page refuses.
- Scenes are data: everything the runtime used to hard-code for one song (who plays whom, facings, partners, the
  floor, keep-out boxes, the default scene, the build script) lives in `scenes/<name>/world.json` and
  `scenes/stage.json`. Session files (client log, speech cache, bundle, update notes) go to `<scenes>/_stage/`.
- One server per port: a busy port is refused (Windows let two servers bind one port and split the live link).
- Pins while following: `stage_follow_anchor(scene, person, joint="hips", to=<seat object | [x, y, z] | "here">)`
  (and "📌 Pin hips" on the Follow panel) keeps a seated person's hips on the seat, facing their own way: the user's
  head only bends the spine (direction, so sitting lower or taller does not sink or lift them), the hands drive the
  arms, the feet plant in front of the seat or on a rung under a high one, or on their own pins (`joint="feet"`).
  Walking clearly away from the seat lets go. The Follow panel shows the pins and that talking is not recorded
  without the phone gesture.
- Every Follow is recorded, in memory: the newest 180 s of the last Follow wait in the page, and after "■ Stop" a card
  offers "💾 Keep as take" (then the usual review: play on them, trim, redo) or "🗑 Discard"; `stage_take_keep_last`
  keeps it for the person afterwards. A liked 61 s follow of the bartender was lost because a plain Follow kept nothing.
- `stage_events(who=)` names an agent that follows a scene; a listener with no name reads "an unnamed agent" in the
  headset, not "unnamed".
- Pins take the pointer ray: a to-do pin's card (and its diamond) stops the ray, lights its border, and a pinch opens
  its note, so pins can be worked from a few steps away (the ray went through the card onto what was behind).
- The version card in front of the person shows on entering VR, when the server restarts, and when listening flips
  between somebody and nobody; no longer each time one listener of several comes or goes (an agent polling every
  half minute kept bringing it back).
- Body panels hold still to be read: a panel the person looks at (within 32 degrees) stays where it is, and for 1.5 s
  after; looking at it never counts as the body turning; it moves only when its place has drifted 25 cm for 0.6 s
  (a walk, a real body turn), then eases there. It used to dart away as they turned to read it.
- The thumbs prompt rides on the answering hand: while a spoken question waits (`stage_ask`, now with `sender=`),
  and while the right hand forms a thumb at a panel, a card over the right wrist says who asks, the question, and
  what up and down will answer, filling as the gesture is held.
- Every panel and caption says who it is from: `stage_say(sender=)`, `stage_panel_show(sender=)` (a command's `from`)
  draw a chip and border in that name's colour, fixed per name, and a sender's body panels keep to one side. The VR
  card that goes with a spoken line rides with the person now, titled by its sender instead of "Claude". The
  server's own lines are from "stage".
- Pin touches land: a to-do pin's card takes a fingertip as well as its diamond, a pin re-arms after 1.2 s even
  if the hand stayed close (it needed the tip to leave by 18 cm), the gaze window is 60 degrees, and a tip at a pin
  that does not open it says why (`pin_touch_missed`). A finger gun at a pin does not travel.
- A poke at UI wins over travel: while a hand's fingertip is within 10 cm of a panel, menu or button, or for 0.8 s
  after it pokes one, the finger gun shows no travel arc and its thumb click does nothing (`travel_held` says so).
  Poking menu buttons with the same finger gun that aims travel had teleported the person twice.
- Panels that ride with the person: `stage_panel_show(anchor="body", side="right"|"left", seconds=)` keeps a message just
  out of view beside where their body faces (a new body heading from the head and hands: a held head turn or the
  hands held out turn it, a glance does not), following them as they move; dragging it moves its place around them.
  World panels stay for notes about a place. Speech and new panels wait while the person is talking (a note
  recording, the phone gesture, 3 s after a note): a reply arriving mid-thought had lost them their sentence.
- Presence: the stage knows who is listening. A listener is anything following the live log (`stage_listen` across
  every scene through GET /live/inbox, or `stage_events` with since= on one); the headset shows "listening: <who>"
  or "nobody is listening" on entering VR and whenever it changes. A voice note a listener was handed but did not
  answer within 6 s is announced ("Handed to <who>"); one nobody was handed is answered out loud ("Nobody is listening right now. I
  saved your note.") and kept in `_stage/unread.jsonl`. Hooks (`~/.ismail/stage_hooks.json`; a song's
  `_stage/hooks.json` only when that file trusts its folder) run commands on entered_vr, left_vr, voice_note and voice_unheard. GET /live/presence, `_stage/presence.json`,
  `stage_presence`. The page also says when the server behind it restarted. (After six notes went unheard.)
- Desktop movement: the wheel steps forward where you look (along the floor in walk mode), the orbit pivot
  travelling with the camera, so zoom no longer shrinks the orbit until the wheel stalls; walk and key speed scale
  with the scene (3.5 m/s in the Crossroads club, was 1.2), Shift 3x.
- Scene lineage (M75): a scene's world.json may say `derives_from` (and `pass`, `pass_env`, `assets`).
  `stage_scene_export` builds a derived scene from its ancestor's full build, then its line's passes in the bridge
  (`VR_PASS`), the line's edits merged (the variant's win) and the Quest diet in the bridge (`VR_DIET`), so a remodel
  or another era never starts from a blockout. `stage_scene_new(name, source=)` makes one: the lineage in
  world.json, the page's pieces copied (trees, names, cues, waypoints), actor bodies from the source. Exports set
  `STAGE_SCENE` so renders go in `renders/<scene>/`. stage.md: "Deriving a scene".
- The stage server stays up (M74): a fixed pool of 32 workers instead of a thread per request (Python 3.11 kept every
  finished request thread, and the page polls 2.5 times a second: the old server died of a MemoryError after about
  40,500 threads), long-polls capped per client, `GET /health` (shown by `stage_status`). A full disk no longer
  stops the live link (state and events stay in memory) or the headset's boot (the last good bundle is served).
- The Blender bridge and the Quest diet (`ismail/stage/bridge`: merged groups, triangle and texture budgets,
  MakeHuman skin and mask-map fixes) ship with it. three.js r180 is vendored (MIT).

## 0.3.0

### Verify from the person's side; small fixes

- SKILL.md non-negotiable: every piece of work names what the person will see, hear or feel, is checked from their
  side, and is reported "verified" with the evidence (or "not verified yet" and what to look at), never a bare
  "done". Ops that change what the person perceives reply with what verifies it.
- `api.call(name, /, **kw)`: the op name is positional-only, so ops that take their own `name` argument
  (`mimic_measure`, `live_stream`) can be called through it.
- blind-tests.md: check the answer key for patterns before the page goes up.
- instruments.md: VSCO 2 CE file names are one octave low (the harp excepted).

### Install and distribution

- On PyPI: `pip install ismail`, or `uvx ismail mcp` to run the MCP server without installing. New console
  commands `ismail` (the CLI, same as `python -m ismail`) and `ismail-mcp`; `ismail mcp` starts the server.
- Claude Code plugin: `/plugin marketplace add newsbubbles/ismail`, then `/plugin install ismail@ismail` loads the
  server and the composing skill together (`.claude-plugin/`).
- Listed in the official MCP Registry as `io.github.newsbubbles/ismail` (`server.json`); a tag push publishes to
  PyPI and the registry (`.github/workflows/publish.yml`).
- `Dockerfile` (stdio server) and `glama.json` for directories that build and inspect servers; `CITATION.cff`.
- The README, the plugin and the client configs install from GitHub (`uvx --from git+https://github.com/newsbubbles/ismail ismail mcp`),
  which works whether or not a release is on PyPI. README: an "If you are an AI agent" section.

### Roadmap

- ROADMAP.md: where ismail is going (worlds, visiting each other's, hands and 4D editing, agents in the room,
  sound, pictures, the person, a hub for shared work) with a status on each; a "Where it is going" section in the
  README.
- SKILL.md: share what would help others (a fork and a pull request, after the inclusion review); development.md:
  sharing back from any user's project; user-experience.md: pictures have a vernacular too.

### Live output follows the device

- The live output follows the system default (`device='default'`): a Bluetooth speaker that connects mid-set takes
  over within a few seconds, and a device that stops taking audio falls back to the default. `live_device` moves a
  running set to another output by hand. The timeline, queue and audio mixed ahead carry on (about a second's gap);
  `live_start(follow_device=False)` keeps the old behaviour.
- The handoff scanner skips `history_src/` backups.

### Live: stream to a scene, controls

- `live_stream`: the master, a bus or a deck as raw PCM (int16 stereo, 44.1 kHz) over a GET on the engine's
  localhost port, each with its own safety limiter; a listener that falls behind loses its oldest audio and never
  slows the set. For a VR stage that plays each object's own bus at its place (relayed by the page's server).
- `live_map`, `live_control`, `live_controls`: a named control (a knob, slider or switch) mapped once to a track,
  bus or deck volume, a deck EQ or an effect param, with linear, log, switch or raw scaling; moves apply in the
  engine at once and are logged by bar, and read back as `[bar, value]` automation points.
- A streamed bus or deck that goes quiet sends silence instead of nothing, so every listener stays in step (the
  Crossroads stage had to fill the gaps itself), and `live_stream(project, name=...)` works (the op's `name` clashed
  with the dispatcher's).

### The stage in the skill

- `references/stage.md`: building a scene with a person inside it, from the Crossroads build: which surface for
  which decision, eye exams for pictures, bodies and contact, contact with a person in a headset (every interaction
  a sound, the person's names for things, a first-time tutorial one gesture at a time), shared-editing rules that
  never lose their work (no save before the scene has loaded), agents first.
- user-experience.md: when the person is inside the work. SKILL.md points to stage.md.

### Placement offsets

- Sounds land by their start; music lands on an anchor. A note can now be nudged off its beat in milliseconds:
  `'0 C4 1 100 @-40ms'` (negative = earlier), and a whole part with `track_set(offset_ms=-35)`. The note keeps its
  beat: `notes_read` shows the `@`, the roll stays on the grid, quantize and copy keep the nudge, and
  `notes_transform(offset_ms=)` sets it on many notes. Automation stays on the song's time; audio clips move with
  the track offset. A nudged note is the same as a note written at its nudged time, in the studio, in any window
  (bit-identical), on a deck (live parity) and in the video sync. A note nudged before 0 s starts at 0 s and the
  render reply says so. Live clips read `@` the same way. From tambopata, whose player was time-warped to land on
  the beat and sounded synthetic.

### The person: lexicon, objectives, user-experience reference

- The lexicon: `lexicon_note`, `lexicon_find`, `lexicon_view` keep a two-way map between the person's own words for
  what they hear and see ("boxy", "too clean") and ismail's terms (ops, parameters and their direction, effects,
  measurements), with the song, the moment, the craft the word belongs to (composer to colourist) and whether the
  change worked. Lookups go both ways; the view reads as a learning curve (the share of trade words by month, the
  crafts a vocabulary grows in). One local, append-only file shared by every session (`songs/_user/lexicon.jsonl`
  or `$ISMAIL_LEXICON`), never committed; a `who` per entry for other people's feedback. Words about the work only:
  never emotion, mood or health.
- Objectives: `project_new(objective=)` and `project_set(objective=, objective_by=)` record what a piece is for, in
  the person's words, with history; `project_info` shows it. `project_new(derived_from=)` carries the original's
  objectives and lineage into a version (intent provenance).
- Skill: `references/user-experience.md` (the lexicon, exams and pages, objectives, consent, what never to
  record), a SKILL.md non-negotiable, and a THE PERSON section in `guide`.

### Measure first, in the tools

- Every track says what its sound is modeled on: `project_info` lists each as measured (a mimic profile, a measured
  library voice, a sample imported or extracted from a recording, a fit), designed (on purpose) or unstated, and
  `render` names the unstated ones with the ops that measure. Fits record it themselves (`instrument_fit`
  apply_to_track, `track_fit` apply); the new `track_model` op records an example the tools could not see or marks
  a sound designed. Agents skipped measuring when the rule was only in the skill, most of all after a context
  summary; the tool replies keep it in view.
- Library voices state their provenance in INFO (`measured` or `designed`).
- `guide` opens with MEASURE FIRST.

### The machine: a line, and priority from the user

- `python -m ismail.machine run --wait 30m` (and `slot(..., wait=)`) stands in line for a heavy-job slot instead
  of being refused, and a job that does not wait yields to every waiter ahead of it. The board lists the line.
- `python -m ismail.machine priority <session> --for 3h --by "the user"` puts that session first in line until it
  expires (`--clear` ends it); it needs `--by`, since only the user gives it. Priority orders the line only: the
  heat limit, the busy CPU and the memory reserve hold for everyone (`--force` skips the heat limit and was the
  only way to go first; the voice session asked for this to finish its blind exams).

### What to reach for when

- SKILL.md: "What to reach for when", one table from the situation to the op, reference or rule (the start of a
  task and every compaction, measuring, real instruments, eye and blind exams, the person's words, sources and
  credits, versions, the shared machine, live sets, sharing back). The tools say back what is missing; the table
  says what to reach for.
- README, "If you are an AI agent": measure first, then ask the person's senses with exams (an eye exam for a
  direction, a blind exam to know when you are done, from one note up to the full mix).
- blind-tests.md: why exams (the person's senses as data, their words into the lexicon) and climbing from the
  smallest unit, since a test pitched too hard gives no direction.

### Credits name what the piece uses

- `credits` credits only the sources the piece uses: a row is used when a part's model names its file, its folder
  of takes or its id (a video id naming a stems folder), a mimic profile or a sample came from it, or its table's
  'in the song' column says so (prose like "**in the song**: the drum kit" or "not yet" reads as yes or no).
  `consulted=True` adds the rest under "Also consulted". A plain list that repeats a table's rows (a SOURCES.txt of
  video ids) is left out, and a table under its own heading keeps it.
- `track_model(on=[...])` takes several sources for one part (a forest bed from two recordings, a voice
  cross-synthesized from bird takes and a player's stem), and a folder of takes (`on='ref/birds/potoo'`).

### Live: the set says when it runs out

- `live_status` adds `RUNWAY ENDED N bars ago` when a set that has played has had nothing new queued for 8 bars
  (what loops on unchanged, or that it rings out), and `THIN for N s` when one track is left where the set has had
  several, or the mix sits 20 dB under its usual level, for 30 s. A set ran 10 minutes on one hat loop after its
  outro forgot to stop it, and `SILENT ON AIR` never fired.

### Sources and credits in the tools

- `project_info` lists a song's sources: the recordings, videos and scores in its `ref/` folders, which of them
  have a row in a SOURCES file, and rows without a licence or an author. `render` names files with no row. Stems,
  separations and analysis projects are skipped; a video's id in a SOURCES row covers its download and the wav
  made from it.
- `credits` (new op, 98 tools) writes `CREDITS.md` at the song root from the SOURCES tables (approval, local path,
  date and note columns left out), each part's model grouped by source, and the lineage. It says whether the piece
  plays recorded audio (imported samples, audio clips) or holds only measurements, and lists what it could not
  credit.
- `track_model(on='ref/birds/x.mp3')` takes a file in the song's `ref/` folder: any `on` starting with "ref" was
  read as the project reference and refused, and paths were looked for only under `proj/`.

### Skill: provenance, the deliverable, the machine-aware performer

- SKILL.md: provenance is kept from the first download to the public page. Every source a song uses, measuring-only
  ones included, gets a `ref/SOURCES.md` row (who made or played it, licence, what was measured); every track says
  what it is modeled on; performers and communities measured from recordings are credited; public pages take their
  credits from `SOURCES.md`. instruments.md and music-video.md log sources and assets the same way.
- SKILL.md: long work stays on its deliverable. `PROGRESS.md` at the song root, read at every start and after every
  compaction; reread the story before designing for it; a tool is a side track unless it unblocks the next step.
  music-video.md: the cast comes from the story, never from the track list, and effort follows the cast's order.
- live.md, "Watch the machine as well as the room": lessons of a 16-hour set (guards keyed on audio trouble,
  tracked watchdogs, shrinking the rig under load, one deck on a busy machine, the set on the person's clock, pause
  and resume as a move, borrowed voices checked before they go on air).

### Fixes from song handoffs

- Render memory: a track's whole output stays in memory only while an effect reads it (a sidechain or vocoder
  source), and stems are kept from their first to their last sound as float32, built full length when read. A
  40-bar song of 16 one-note tracks: peak 2856 -> 404 MiB, 1835 -> 73 MiB held after. A 146-bar song of ~70 tracks
  (tambopata) had needed ~20 GB and ran out of memory.
- `mimic_measure` measures a short note (a 0.3 s panpipe): its attack calibration read the note's level over a
  window that started after the note ended and produced a non-finite buffer.
- `analyze_timbre` (and every analysis reading a source in stereo) works on a mono file: both channels read the same.
- Live decks run the song's master chain (its limiter, compressor, any master effect), as its studio render does:
  `live_load` dropped it, so a deck played about 4 dB quieter and unlimited. `live_status` shows it on the deck line.
- A looping performer clip no longer hands its voice a negative song time: the context carried over the loop point
  sat at negative clip beats, and a voice that seeds on the song time (the birds call voice) failed on it.
- `live_transition` takes the old deck off air when it ends, so the next song loads onto it (it was refused, and a
  helper that crashed on the refusal left a set silent for 9 minutes). `live_status` says `SILENT ON AIR` when a set
  that has played sounds nothing for 10 s. `live_deck(cue=False)` describes the deck as it will be. `live_load`'s
  doc says a cued deck plays from `at`.
- `djkit.gap` brings each target back to a level (`level_db`, a number or per target) instead of 0 dB, and
  `Set.gap` uses the gains the set has given each track: a set running its tracks at +4 to +8 dB had every drop land
  under its build.
- The machine governor: `run --est` takes a unit (`10m`, `600s`, `1.5h`; a bare number is minutes and one above 240
  is refused, since `--est 600` meant as seconds showed a 10-minute render as 585 min). The board shows a `python -c`
  job by its first line and how far past its estimate a job runs.

### Roles and the migration process

- `development.md`: the roles as a multi-agent system (the user, song agents, the dev agent, subagents: what each
  owns and writes, and the contracts between them); the inclusion review before anything goes into the public repo
  (general beyond its song? measured from the user, who must say yes first? fitted to a commercial record or
  carrying a brand name, held for the user's decision? provenance recorded); preparing a voice or an engine for
  ismail in eleven steps (copy never move, strip the song out, render the same anywhere, live parity, INFO as manual
  and provenance, nothing existing changes by accident, tests, cost, docs, evidence in the PR); how to take an API
  change a song asks for (find the workaround and its cost, check it does not exist already, extend before adding,
  design the text first, close the loop, keep old names, prove it on the asking song); lessons from running the loop.

### The shared machine

- `ismail.machine`, the governor for one computer shared by many sessions (2026-10-02: six sessions stacked heavy
  jobs on a laptop GTX 1080 until it sat at 92 C pinned at 139 MHz and the user stopped everything). Op
  `machine_status` (and `python -m ismail.machine`): GPU heat, clock and throttle reasons (an idle card at 139 MHz is
  not trouble; a thermal bit or 85 C is), CPU, free commit, and every heavy job running in any session. Heavy jobs
  take slots on a board in `songs/_machine/`: one GPU job and two CPU jobs machine-wide, a live engine on air holds
  one. render, separate (GPU slot when it runs on CUDA), mimic_measure, instrument_fit, track_fit and live_parity
  take a slot and refuse with what is running, whose it is, when it should end, and what to do; a render whose
  memory estimate does not fit the free commit is refused instead of dying. Commands outside ismail run in a slot
  with `python -m ismail.machine run --gpu|--cpu -- <command>` at below-normal priority. Heavy jobs cap numeric
  threads at 2. The test suite takes a slot too.
- The governor also refuses a new CPU job while the CPU is 80% busy or more over 2 s, whoever is using it, and names
  the top processes: most load on this machine is not on the board (the desktop app, a node server). The board shows
  the top processes.
- Skill: "The machine is shared" is a non-negotiable (check before anything over a minute, one heavy job of your own,
  a hot GPU is not a free CPU, size jobs to the question, no sleep-poll loops, never leave heavy jobs running
  untold); development.md: touched tests locally, the full suite in CI; announcements never ask for work.

### Roles and collaboration

- Skill: "Your role, and where things live": making music is the default role and writes only inside
  `songs/<slug>/` (no edits to `ismail/`, `skills/`, tests or other songs, no git in the ismail repo); a missing
  capability is built in the song and listed in its `HANDOFF.md`; one folder layout for every song; song
  checkpoints with a git repository inside the song folder. The `guide` op opens with the same rule.
- New `references/development.md` for changing ismail itself (only when the user asks): a worktree per topic, the
  collaboration must-haves (what is not yours is not touched, nothing unmerged is deleted, no stash in a shared
  repo, proof and docs in every commit, the user merges), migrating from a song's `HANDOFF.md`, its format,
  and studio/live parity: every engine change says which side it touches, how live follows, and what proves it.
- `live.md`, from a 40-minute blues set: only proven sounds go on air (no voice written during set prep and never
  fitted or ear-tested), first sound within minutes, never downgrade a sound in silence, a song plays live through
  `live_load` and is never rebuilt by hand, and anything that writes runs on a copy, never in another song's folder.
- `blind-tests.md`: ear-test pages are always hosted on localhost and opened in the app's browser pane, take answers
  with a Submit button that writes them to a file, and are checked in the pane before the user sees them.
- Skill brought up to date with the live parity work: phrase voices no longer run an amp inside the voice (a
  guitar is `electric` with its rig on the track, live as in the studio); the performer signature takes `beat0`;
  how to write a performer's expression in the studio (`inst.lane.<name>` automation); the rig effects, presets,
  `electric`'s half-step-down tuning and `kit70`'s drum map; five rules for a voice that renders the same in a
  window, live and in the whole song (from `songs/tambopata`); `live_parity` for checking a song on a deck; what
  `live_status` and a deck's "not live" list now say; render speeds of the library performers. Fixed: the limiter
  ceiling (-1.0 dB for mp3 everywhere), the eye-exam page (Submit, not a copy button), wah as an effect sweep, not
  a lane, and where the engine looks for a song's voices. SKILL.md: call `guide` before writing a measuring
  script. The `guide` op's live text matches.
- The migration loop (`references/development.md`), the dev role's standing job: intake with
  `python -m ismail.handoffs` (every handoff section new or changed since the last mark, `--full`, `--mark`),
  triage into `songs/_migration/LEDGER.md`, the user decides, build on a branch, announce merges to every ismail
  session, the song closes the item. SKILL.md tells song agents their handoffs are read and how to close an item.
- The intake names sections by their heading path (one subheading under two elements stays two sections), skips
  caches and backups, and lists a reorganized handoff's old text only with `--all` (a section whose words were
  already in the file is "moved", not new).

### Guitar rig and performers

- Rig effects (`ismail/rig.py`): `fuzz` (Fuzz Face bias shift), `univibe` (four-stage LDR phaser with lamp lag),
  `amp` (Marshall-style tone stack after Yeh and Smith 2006, push-pull power stage with sag), `cab` (min-phase
  cabinet with a mic blend), `rotary` (Leslie with ramping rotors), `tape` (head bump, wow, flutter, hiss), `wah`
  (resonant band-pass). `fx_help(type=...)` documents each. Live runs them as block-by-block twins (below).
- Performer voices in the studio: a voice module with `perform()` and no `voice()` renders a whole part at once
  (strings that ring on, legato, slides, whammy), with expression lanes from automation `inst.lane.<name>`. Live
  already played them from clip `expr` lanes; one mechanism now: a deck turns a song's `inst.lane.*` automation
  into its clip's `expr`, and `live_load` marks a song's performer tracks (decks had played them note by note).
- Library voices `guitar/electric` (electric guitar or bass: waveguide strings, pickup comb and resonance, lanes
  for bend, vibrato, mute, slide, level) and `drums/kit70` (a 1970 kit as modal resonator banks), both performers,
  fitted in a late-60s guitar record study (20 of 22 single lead notes passed a blind exam). Presets
  `strat70_lead`, `strat70_rhythm`, `strat70_rotary`, `pbass70`, `kit70`; each voice's INFO `rigs` holds the fx
  chain its presets were fitted with, and `voice_help` prints them one line each.
- Skill: `references/blind-tests.md` (the eye exam and the blind exam: fair clips, reading the answers, the
  song-level checks single notes miss), and the guitar study's lessons in sound design (register first, harmonic
  profile, stem bias, rig order, fast rig fit), composition (phrase placement, band feel, bends to chord tones),
  recreate (straighten a drifting record, round-trip every sensor) and listening (spectrogram pairs; long-window
  numbers are blind to fakeness).

### Live, from a one-hour set

- DJ kit, `ismail.live.djkit`: step strings to notes, cycles built bar by bar inside one clip, notes on another
  metric grid, effect moves (sweep, throw, gap, pump) addressed by effect type, and a `Set` that logs every call
  with the clip ids per section so a queued section can be cancelled.
- `live_fx`: `index` may be the effect's type (`'filter'`, `'delay:2'`); `moves=[...]` schedules a whole
  choreography in one call; `clear=True` cancels a track's scheduled moves (each param holds). A sweep can target
  a chain scheduled with `live_track(fx=..., at=...)`; the swap drops only the old chain's sweeps and keeps volume
  moves (it used to drop everything, a loaded song's volume curve included).
- One `at` vocabulary: `now` and `asap` mean the same in every live op.
- Problems between calls (a note dropped as too loud, a warm-up error, a stalled device) are added to the next
  reply of any live op. `live_status` shows each level's loudest over the last 10 s and a STALLED line when the
  device stops asking for audio.
- `live_start` names other engines still running on the machine (a registry in `~/.ismail/live`); `live_stop`
  waits for the engine to exit and kills it with its workers when a dead device would hang it.
- Skill (`references/live.md`): reading the audience, your latency, the pre-flight, dynamics with the kit, a set's
  folder layout, playing for a screen recording.
- Fix: the live engine crashed at startup since the rig effects were registered (its effect warm-up tried to
  build a live block for studio-only types); it now skips them, and they bake in the workers as before.

### Live and studio parity

- Fix: a note still sounding when a window starts (a drone, a pad, a held string) now plays in a studio render of
  a bar range and on a deck loaded with `bars`; both dropped it, and the deck said "silent in range". A note that
  rings on at least a beat into the window comes in on its first beat.
- A deck loaded straight on air is held off air until a whole bar of it is rendered, then goes on air on that bar
  line; it used to go on air with nothing rendered and lose its first bars in silence. `live_status` shows the
  hold, and the next reply says when it slipped and when it went on air.
- Renders go to the workers earliest-needed first, from an engine-side list, and a render no queued clip wants any
  more is dropped before a worker spends time on it. A clip queued for later renders its first pass from the
  moment it is queued, so loading ahead buys render time.
- Notes that never sounded because their render came back too late are counted (`live_status`: "never sounded")
  and said per track in the next reply; the late counter used to read 0 while bars played silent.
- Warm-ups render a short tail: each one had rendered 8 s through the track's baked rig on every worker, ahead of
  every real render, and a 23-track deck spent half a minute warming up.
- Fix: a deck's cue flips (`live_deck(cue=...)`, a transition's on-air) now land on the bar line the listener
  hears; they came about 90 ms early.
- The guitar rig runs live: fuzz, univibe, amp, cab, rotary, tape and wah have block-by-block twins
  (`ismail/live/rig_blocks.py`) running the studio kernels, held to the studio by `tests/test_live_parity.py`.
  They were baked into every rendered note: a guitar rendered at 0.8x realtime and a deck of Crossroads never got
  on air (219 notes lost); now it plays from its first bar with nothing late and the mixer at about 31%. Per-note
  baking also stacked one amp's hiss per note (+7 to +9 dB on the noise floor; now equal to the studio's), and a
  song with `tape` on a bus can load on a deck. Rig params move with `live_fx` (wah `pos`, rotary `speed`).
- Studio rig changes so a live twin can be exact (all inaudible): amp and tape hiss draw one noise stream per
  channel (a different noise, same level); tape wow is a causal running delay (the output sits 14 samples later);
  auto-wah follows its recent peak instead of the whole part's 98th percentile (within 0.1 dB).
- Performer voices play live in bar chunks: each bar renders with the second of the part before it as context
  (held notes from their real start), cut and crossfaded at the bar line; a looping clip takes context from its
  previous pass, a deck's section from the 8 beats before its window (a held note is no longer struck again on the
  window's first beat). Crossroads on a deck against the studio: envelope correlation 0.999-1.000 and bands
  within 0.5 dB on bass, rhythm, lead and drums (0.96-0.99 before; the lead lost 21 dB at 125 Hz at the window
  edge). kit70 renders at 5x realtime instead of 0.5x (no 8 s tail per hit).
- `electric` and `kit70` key their randomness per note on the song beat (`perform(..., beat0=)`), so any slice of
  a part, a studio bar range included, plays exactly as the whole part. One generator drawn note after note gave
  every slice a different performance (a kit's hi-hat changed timbre with the number of notes). Studio renders of
  songs using them get a new realization of the same humanization. `skills/.../sound-design.md` says how to write
  a performer this way.
- Deck notes keep 10 significant digits (6 rounded long sections' timing).
- A track whose amp or tape hisses keeps hissing through rests up to 8 s before it sleeps, as in the studio.
- A big `live_queue` no longer stalls the mixer: the batch's render estimates and event groups are planned before
  the engine lock is taken, the scheduler places at most 128 events per pass (earliest first), everything alive at
  engine start is frozen out of the garbage collector's full sweeps, and the GIL switch interval is 1 ms. An
  8000-note queue: the mixer's longest wait for the lock 256 ms -> under 25 ms, collector pauses 133 ms -> 20 ms,
  mixer peak about 1000% -> 130-180% of real time.
- The live mixer keeps every effect's state out of denormal floats (a fuzz left in silence decayed into them and
  took a guitar chain to 100% of real time), and a track whose amp only hisses goes dormant like a silent one.
- A standing parity check: op `live_parity(song, bars)` renders the bars in the studio and plays them on a silent
  engine of its own, then compares each track, bus and the mix (level, envelope, octave bands, residual), judged
  from the window's second bar. `tests/test_live_song_parity.py` holds every instrument type and library voice to
  it: voices that are not performers match the studio sample for sample, performers by ear.
- Synths and drums seed their random phase and noise on where a note sits in its bar, not where it sits in the
  render buffer: a studio render of a bar range now sounds as those bars do in the whole song, and live plays it
  sample for sample (it was a different take of the noise and phases each time). Studio renders of synth and drum
  parts get a new realization; nothing else changes.
- Code and mimic voices get each note whole even when a render window ends first: a voice whose noise draws or
  filters depend on the length it is given (the `sfx` voice) sounded different near a window's end and live.
- Fix: an engine rendering inline (`workers=0`) now finds a loaded song's sampler sounds; its samplers were silent.

## 0.2.0 (2026-09-30)

### Live

- **Live engine** (`ismail/live/`): a separate process per folder plays a queue of clips in real time while the
  agent edits it. Ops: `live_start`, `live_stop`, `live_status`, `live_track`, `live_bus`, `live_fx`,
  `live_queue`, `live_cancel`, `live_view`, `live_listen`, `live_record`.
- Clips loop until replaced; launches quantize to the beat, bar or phrase (`next_4`, `bar:N`, `after:<id>`,
  `after:#k` for a whole arc in one batch). The reply gives each clip's landing bar and the runway.
- Render ahead: worker processes render each note or phrase before the playhead (identical notes render once);
  a clip that cannot render in time lands later and the reply says so.
- Effects, send buses, sidechain, duck and vocoder from live tracks; `live_fx` ramps any automatable param.
- Safety chain on every output: trim, loudness rider, lookahead limiter, ceiling, set from the environment.
- `live_listen` analyses the last bars played, a deck, or a finished `live_record` take.
- Decks: `live_deck`, `live_load` (a whole ismail song on a cued deck), `live_transition` (blend, bass swap,
  filter, cut), a DJ strip per deck (LR4 isolator, filter knob, fader, transpose).
- Studio and live: studio code is the source of truth. Effects run live as block-by-block twins
  (`fx_blocks.py`, `dsp_blocks.py`) held to the studio versions by `tests/test_live_parity.py`; an effect with no
  twin is baked into each rendered note or phrase. Voices with `perform()` play whole phrases live, with `expr`
  lanes (bend, vibrato) on clips.
- mimic profiles and code voices play live.
- Install `.[live]` (sounddevice) to play to speakers; `device='none'` runs without audio out.
- A deck plays a song the way it renders: a bus that tracks play through keeps its reverb or delay as an insert
  (dry passes; live buses had been wet-only, which took the drums out of a song with a reverb on its drum bus),
  and the song's automation comes over (fx params and track and bus volume as ramps, instrument params rendered
  with the notes, the master fade), repeated every pass on a looping deck. A track with instrument automation
  renders its section as one event, sent to a worker one pass ahead.

### Measurement

- `analyze_grid`: the snare on 2 and 4 votes for bar 1; reports the record's tuning offset and swing.
- New ops: `ref_retune` (retuned analysis copies of an off-pitch reference), `analyze_swing`, `analyze_kit`
  (the pieces of a drum kit and each one's pattern, by NMF), `analyze_sections`, `levels_from_ref` (faders from
  the reference's stem balance).
- `analyze_sections` flags a build (2 dB or more under the climax on average, peaking within 3 dB of it), not a
  drop that goes on at the same level.

### Voices

- mimic: instruments measured from recordings (`mimic_measure`, the `mimic` instrument type), with leave-one-out
  checks; wandering vibrato, swells, measured room, attack maps, sympathetic strings.
- Voice library in family folders; measured string profiles ship built in.

### Skill

- Live reference (`references/live.md`): running a set as a DJ loop, small edits over whole sections, a runway
  before every question, metric modulation for style changes at a fixed tempo, performers and phrase voices.
- Recreation lessons: measure tuning, swing, kit pieces and fader balance; a record's drum kit as measured
  models; phrase length before tiling; one-part-at-a-time A/B.

## 0.1.0 (2026-09-26)

First public release: an agent-operated DAW with a CLI, an MCP server and a Python API. Notes, patches,
effects and automation in as text; levels, spectra, drum lanes, piano rolls, chords, structure and stored
reference comparisons out as text. Code voices, drum synths, samplers, stem separation, instrument and track
fitting, the optional music-video pipeline, and the agent skill.
