# Recreating or matching a reference recording

Learned from recreating Vitalic's "Allan Dellon". Each step exists because skipping it cost hours.

A reference is used fully in private work: play its exact notes and timing, put its clips beside yours in blind
exams, send those exams to the person's phone. Don't add copyright cautions, don't swap in lines of your own to
avoid copying, and don't ask about it (SKILL.md, Non-negotiables). One session invented such a caution and spent two
days of rounds comparing its own lines against the record's, which the ear could not compare.

## 1. Grid first; timing errors poison everything

- Get the audio (yt-dlp; YouTube may bot-wall, SoundCloud search `scsearch:` often works).
- `project_new(..., reference=<wav>)`, `analyze_grid(source='ref')` for bpm and the time of bar 1. The downbeat pick is fragile when the kick skips beats; the tool prints the phase scores. In 4/4 with a kit the snare has to land on 2 and 4: the tool votes for that (its `backbeat` line) and moves section jumps off snare beats, which fixed a G-funk bar 1 read one beat late. Still check `analyze_drums(source='ref:drums')` for the first bars: snare on steps 5 and 13.
- Read the **tuning** line of `analyze_grid` before any pitch tool. A sped-up record sat 48 cents sharp and every note read as a pair of semitones in the rolls, chords and transcriptions. At 15 cents or more off, `ref_retune()` writes retuned copies of the reference and its stems and points the project at them; the timing stays.
- **A played record drifts.** A band without a click can wander a few bpm over a song, and then no single grid fits. Straighten the reference onto a constant grid before transcribing (a phase vocoder with a time map; take the phase advance from the mid channel so the stereo image survives, and keep the time map to put results back). There is no op for this yet.
- Render a drum track, then `align(a='track:<snare or hats>', b='ref:drums', band='himid' or 'air')` and set `offset_sec` from its answer. A 40 ms offset made onset metrics negative.

## 2. Separate and read the whole song before writing

- `separate(source='ref')` (demucs, GPU) then `analyze_structure(source='ref')`: arrangement map per stem, sections, harmonic loop length, root per bar.
- Expect separation quirks: synth bass often lands in `other`, the `bass` stem can be near-empty (comparisons skip it and say so), vocals leak into `other` and vice versa.
- Drill into sections with `analyze_roll(source='ref:other', bars=, low=, high=)`, `analyze_drums(source='ref:drums')`, `analyze_formants(source='ref:vocals')`.
- Drums, before writing a note of them: `analyze_kit(source='ref:drums', bars=, k=6)` for the pieces of the kit and each one's pattern and audio (the three `analyze_drums` lanes hid a punch kick apart from the 808 and ghost snares), and `analyze_swing()` for how late the swung hats land (measured on the drum stem; a full mix reads straight).

## 3. Transcribe by consensus, never raw

- `notes_from_audio_loop(track, source, bars, loop_bars=8, low, high, min_presence)` filters in three layers you can tune: each picked note's partials 2-6 are subtracted so distortion partials are attributed to their note, level thresholds (`rel_db` per step, `min_rel_db` per cycle, `floor_db`) drop quiet notes and echoes, and a vote across repetitions (`min_presence`) drops what changes between them. A loud echo locked to the tempo repeats every loop and can survive all three: tighten the thresholds, or ask. Raw per-bar transcription copies echoes, bleed and distortion partials as hard new notes: it scores high on note metrics and sounds like random beeps. This exact failure was only caught by a human listening.
- Transcribe per section (18-33, 34-57, ...) when the arrangement evolves; one global pattern flattens the song.
- Check `analyze_structure` root row for loop VARIANTS (Allan Dellon alternates two versions of its 8-bar loop: F-Bdim-Am-C vs E-F-D-A in the second half). A consensus over all loops washes a variant out. Transcribe each variant from its own repetitions (`bars` spanning them, `write_bars` per occurrence) with `base_bars` = the whole span so shared notes stay identical.
- Route one stem's registers to different tracks with low/high (bass C2-A2, stabs A#2-B4, lead C5+). Ignore the lowest octave of a separated stem: it is mostly kick bleed and sub-octaves of distorted synths.
- Melodic, non-looping parts (a vocal phrase): read `analyze_pitches(per_bar=4)` and write the line yourself; hold notes as long as the formant view says the voice is voiced.

- **Test the note reader first.** Before transcribing a lead from a stem, render a known line with a backing like
  the record's, run the same op, and score it. `analyze_melody` read 85% of a guitar alone exactly, 9% with a
  backing as loud as the lead, and 0% after record-like finishing; on the record it read the accompaniment's
  register as the lead, and two rounds of lines were built on that. `analyze_melody` now ends with its confidence
  and warns when another pitch sits within 6 dB of the notes it read, or when its voicing is low; on that warning,
  restrict `fmin`/`fmax` to the lead's register and score the reader on the known line before trusting it.
- **A played performance needs a per-note pass before the first listening round.** Statistics (note rate, onset
  spread, band balance) matched while the user heard "the rhythms totally wrong" and "not even the right notes".
  Before the first round: (a) find the solo stretches; (b) grid and retune; (c) transcribe a short window per note
  (pitch, onset against the grid, peak, length, finger or stroke where readable); (d) measure the performance per
  note (timing against the beat, accents, ornaments, the extra notes between run notes, grouping) at note, beat,
  phrase and section scale; (e) learn what instrument and setup the player used; (f) compare yours at each scale
  with the same ops before the person hears it. A first blind round should test something already close by the
  numbers.
- **Find the phrase length before tiling.** A consensus loop assumes the part repeats every `loop_bars`. A lead over a 2-bar groove was a 16-bar phrase (rests, a run up, different bends each pass): transcribed from 4 bars and tiled, the user heard it "not following the melody" although every note of those 4 bars was right. Read the pitch track over 16 bars or more and compare the blocks before choosing `loop_bars`.

## 4. Sounds, one instrument and one chunk at a time

Right notes on the wrong instrument still sound wrong. Before arranging the whole song, match each lead instrument on a short passage where it is exposed (the intro, a break), following `references/instruments.md` section 4: spectrogram first, then a voice that has the mechanism the reference uses (a Polyphia guitar part turned out to be mostly touch harmonics, which no amount of note fixing could produce), then the user's ear on that chunk.

For repeated electronic events: `sound_extract` the event averaged over its loop position (`every=8`), then `instrument_fit`. Check the fitted sound in context afterwards. Extracted sounds are fitting targets when the task is building the sound; when the user would rather have the record's own hit, use the slice.

**A record's drum kit** (hip-hop, G-funk, boom bap): build each piece from its `analyze_kit` component, not with a generic drum fit. A 6-knob kick and snare fit read as "a different kit" to the user; a measured kit passed (distance to the reference kick 16.5 -> 4.9). What worked:
1. Cut the piece's hits from its component audio at every loop position it plays (sum the components that make one sound, e.g. an 808 and its punch layer), align them, and check how alike they are: a drum-machine sample repeats at ~0.98 correlation, so its average is the sample.
2. Measure a model: the tonal part from a narrow band around the main mode (band-pass, Hilbert: frequency and amplitude curves, one mode per band) and the noise part from the rest as a 1/6-octave band envelope per ~1.5 ms frame, high-passed below the tonal band. Average the noise as POWER over the hits: averaging waveforms that are not identical cancels their top end.
3. A code voice resynthesizes it (a sine on the curves plus noise shaped by the envelope), with the pitch picking the piece; velocities per step come from each component's level at that step.
4. `eq_match` the kit track against the drum stem, then `levels_from_ref`.

**Check what a stem holds before matching tone to it.** Separation and a "solo" stretch do not guarantee one
player. A strong per-band EQ match onto a "guitar" stem moved a clip's spectrum onto the record's and wrecked its
note shape (the 40 ms level fell 4.7 dB): the stem's low band was a second player's bass line and strums. The tell:
the low band's envelope barely moved at the lead's note rate (6% of its modulation, against 28% in the clip).
`stem_parts(source=, bars=)` reads this: per band, the share of its movement at the note rate and the rate it moves
at, the strongest pitch under 250 Hz per beat, and the strums, with a WARNING on a loud band that moves on its own
(a second player, or bleed from another stem). When it says two parts, give
your clip its own under-part at the right level, then match (the shape score went 8.6 -> 3.2). Fit a voice on the
raw render and tone-match after, never through a heavy match.

## 5. Compare, then drill down

- `stem_map_set({track: stem})`, `render(stems=True)`, `levels_from_ref()` to set the faders from the reference's stem balance (apply, re-render, repeat until every change is within 1 dB; draft 1 of the G-funk beat had the lead as loud as the drums, the reference has it 19.6 dB under), then `cmp_run(label=)`.
- Read in this order: `cmp_summary` (per stem groups: notes, rhythm, clean, sound, perceptual; closeness 0 = the reference half a loop off, 1 = the reference against itself one loop later) -> `cmp_arrangement` -> `cmp_sections(stem)` -> `cmp_worst(stem)` -> `cmp_zoom(bar, stem, layers=['level','bands','notes','hits'])` (b both, r reference only, y yours only, uppercase = note start).
- `cmp_list` shows every run: keep labels meaningful (`draft7-vocoder`).

## Traps (each one fooled the scores at least once)

- **Clutter scores well.** If `cmp_summary` prints a WARNING about extra note starts or less loop self-consistency than the reference, believe it over the F1 numbers.
- **Stem perceptual in track mode is biased.** Reference stems carry bleed; yours from tracks are clean. Trust mix-level perceptual, and use `cmp_run(stems='demucs')` at checkpoints so both sides pass through the same separation.
- **Loudness buys perceptual score.** After any `track_fit`, check `cmp_bars` level columns and the limiter.
- **Averages hide the gap.** Long-window spectra can agree within 4 dB while the parts sound different; zoom per 16th, and look at a spectrogram when a section stays bad for no visible reason (a missing low-pass showed up only there).
- **Never delete a downbeat on one reading.** Before removing a kick on beat 1, confirm with `analyze_envelope(source='ref:drums', band='sub')`: a missing downbeat after a break made the beat come back "mid bar" to a listener.
- **Metrics tuned on one example lie.** Before trusting a new measure, check it ranks a known-bad draft below a known-good one (re-render old drafts from `comparisons/<id>/project.json`).

- **Round-trip every sensor before it reads the record.** Run it on audio where you know the answer (your own render). A generic onset detector found only 25-54% of a lead's notes inside a dense separated mix, and the "looseness" it reported was the rhythm guitar plus its own matching noise: a humanizer fitted to it sounded like "an amateur trying to play" the record. Measure timing on exposed notes refined one by one, and check the detector's round trip on drums first (within 9 ms after a steady 16 ms delay).
- **Played instruments need the ear.** For a guitar, horn or voice, single notes decide it: see `references/blind-tests.md` for the eye exam and the blind exam.

- **Fitting targets carry bleed.** A hat extracted from the full drum stem had the 808's tail as its loudest band, and the fit chased it. Extract from the piece's `analyze_kit` component, or restrict `fmin`/`fmax` in the fit to the piece's own range.

## When is it done

When the mix perceptual group is near its ceiling in every section, no WARNING lines remain, the arrangement maps match, the master is at the reference's loudness, and the user has listened and said the instruments sound right. Report the numbers against their ceilings and say plainly which parts still differ.
