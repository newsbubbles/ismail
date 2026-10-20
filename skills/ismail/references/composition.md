# Writing and arranging in ismail

## Notation you will use all the time

- `notes_write(track, bar, notes, bars=, repeat=, mode=)`: `'<beat> <pitch> <dur> [vel] [@offset]'`, beats relative to `bar`. `bars` sets the span that `mode='replace'` clears and that `repeat` tiles. Write one bar or one phrase, then tile with `repeat` or `notes_copy`.
- `pattern_write(track, bar, lanes={'C1': 'X...x...X...x...'}, step=0.25, repeat=)`: X 127, x 100, o 70, - 45, `_` ties the previous step. Pattern length sets the span. Best for drums and for any repeated rhythmic cell on a fixed pitch.
- **Landing a sound on its beat.** A note sounds from its start, but music lands on an anchor: the attack of a bowed note, the loud part of a bird call, a syllable. When the attack comes late, nudge the sound, not the grid: `@-40ms` on a note (`0 C4 1 100 @-40ms`; measure the lead-in, e.g. the first frame within 10 dB of the note's peak), or `track_set(offset_ms=-35)` for a whole part, both in milliseconds, negative = earlier. The note keeps its beat (quantize, copy and read it on the grid; `notes_read` shows the `@`), automation stays on the song's time, and a deck plays it exactly where the studio does. `notes_transform(offset_ms=)` sets a nudge on many notes at once. Never time-warp a recording to make it land: tambopata did, and the player sounded synthetic. A note nudged before 0 s starts at 0 s (render says so; raise `offset_sec`).
- `notes_copy(from_bars, to_bar, times, to_track, transpose)`: repeat sections, move a line to another instrument, make an octave layer.
- `notes_transform(bars, transpose, velocity, vel_scale, shift_beats, quantize, dur_scale, legato, pitches, offset_ms)`: variation without rewriting.
- `automation_set(track, param, points=[[bar, value], ...])`: fractional bars (17.5 = beat 3 of bar 17). Filter sweeps: `inst.filter.cutoff`, log interpolated. Fades: `volume_db` (an offset on the fader) or `track='master'`.
- A performer voice's expression (`electric`: bend, vib, slide, mute, level; `voice_help` lists each voice's lanes) is automation `inst.lane.<name>` with the same `[bar, value]` points, e.g. `automation_set('lead', 'inst.lane.bend', [[17, 0], [17.5, 2], [18, 2], [18.25, 0]])` for a whole-step bend on beat 3. A deck plays these lanes too.

## Rhythm

Electronic music lives on the 16th grid. Give each part a rhythmic identity and keep it:

| role | cell ideas (one bar, 16 steps) |
|---|---|
| kick | `X...X...X...X...` four on the floor; `X...X...X.......` (drop beat 4, leaves air for a fill or snare pickup) |
| snare/clap | `....X.......X...`; pickups `...X........X..X` alternating bars |
| hats | offbeats `..x...x...x...x.`; 16ths with accents `x-x-X-x-x-x-X-x-` |
| bass | offbeat `..X...X...X...X.`; rolling `X.xX.xX.xX.xX.x.`; root on 1, octave jump on the "a" |
| stabs | syncopated 3-3-2 `X..X..X.X..X..X.`; answers in the gaps of the kick |
| lead | motif in 2 bars, silence in the next 2 (call and answer) |

Rules of thumb: accents (X) on no more than 4 steps per bar per part; a part that plays every 16th needs velocity movement or it reads as a drone; leave the step before a downbeat empty in at least one part so the downbeat lands.

## Harmony and voicing

- The harmony is measured before it is written: the reference's roots (`analyze_structure` root row, `analyze_chords`), or two or three tracks of the genre when there is no reference. Many genres (electro, techno, much house) live on a pedal or one or two chords, and the riff and the sound carry them: then "a pedal, no progression" is the Session Sheet's answer. A stock loop written by habit over such a genre was the first thing the person called basic.
- When the harmony does move, write its roots per bar in the Session Sheet, then derive: bass takes roots (C2-A2), stabs take root + fifth or triad in A#2-B4, pad takes 3-4 note voicings in C3-C6 with common tones held across the change.
- Voice-lead: move the fewest notes between chords; hold shared notes (write them as one long note across the bar line).
- A pedal under changing chords builds tension in an intro; in a genre built on the pedal, it is the song.
- Minor loops that work, when the genre moves: i-VI-VII-i, i-iv-VI-V, i | i-IV | v | v | VI | VI | v | v (the Allan Dellon loop is D | D-G | A | A | F | F | A | A).
- After writing, check with `analyze_pitches(source='track:<pad>', per_bar=2)`: the loudest three notes per half bar should be the chord you meant.

## Melody

- With a reference, read its riff before writing one: `analyze_roll` or `notes_from_audio_loop` on its stem, `analyze_melody` with its confidence line (`recreate.md` section 3), and write from what it plays.
- Build from a 2-4 note motif; repeat it, then vary one thing (last note, rhythm of the tail, transpose by a chord tone).
- Land long notes on chord tones; use passing tones on weak 16ths.
- Range: a lead should span about an octave; more reads as random.
- Space: a lead that plays every bar tires fast. Alternate 2 bars on, 2 bars off, or let a delay (`fx delay time_beats 0.75, feedback 0.4, pingpong`) answer it.

## Feel and phrasing (played parts)

For parts meant to sound played (guitar, bass, live drums), placement matters more than notes. Learned on a 1970 guitar band pastiche; the per-part checks are in `references/blind-tests.md` section 4.

- **No per-note random jitter.** It is what an amateur sounds like; keep it to a few ms. Place whole phrases instead: landing notes tight to the band (within about 8 ms), phrase entries free (on the grid, 35-75 ms late or 25-50 ms early), and the run between re-spaced evenly (a lazy start rushing into the landing, or a push). Move the notes and every expression lane (bend, vib, slide, mute, level) and every effect sweep (a wah's
`fx.<i>.pos`) together with one time warp.
- **The band has a feel too:** a shared slow push and pull per bar, 16ths a little late, a little looseness. That alone moved drums, bass and rhythm guitar from "digital" to "organic".
- **Bends land on chord tones** of the chord sounding when they land, or on a scale tone that is no semitone from a chord tone (which keeps the blues bend from the minor 3rd to the 4th); never more than a whole step. A half-step bend onto the major 3rd over a minor chord sours it for as long as it is held.
- **Each chord its own scale.** One mode over the whole song put a G natural under Ebm9. A double-stop under a held melody note moves by scale steps, as a pair.
- Vibrato on held lead notes: 28-42 cents peak to peak at 4.5-5 Hz, wandering in rate and depth, starting after the note settles.

### A melody that travels through instruments

A long chapter with a fixed groove (focus music, a long build) gets a thread the ear can follow when one line moves
from instrument to instrument instead of new material arriving each section (ledger:S50; asked live: "we should hear
a movement through melody but different instrument too").
- Write one 8-bar line over the chapter's chords. Hand it on at each section: piano, bell, piano, strings, flute, a
  mono synth with glide, strings, bell. Come back to an earlier voice before the end, so it reads as one line.
- Fit each pass with one helper, never by hand: `line(shift, vel, legato, lo, hi)` moves the whole line by octaves
  (whole octaves keep the tune) until it sits inside the voice's range, folds a stray note by an octave, and sets
  velocity and note length for that voice (a bell short, strings legato, glide on the mono synth).
- The range is the measured one, checked before writing: a mimic profile's range is the notes it was measured on
  (`notes[].midi` in its `.mimic.json`), and a sampler's is its lowest and highest sample. Keep a lead three or four
  semitones under the measured top: the top notes come out loud and synthetic.
- Keep everything else the same while the line moves (chords, drums, bass), so the change of colour is the event.
  Check with `analyze_structure(source='render')`: each hand-off should show as a new row in the map.

## Arrangement

- Write the form map in the Session Sheet with an energy number per section. Energy comes from: number of parts, filter cutoff, drum density, register spread.
- Change something every 8 bars: add or drop a part, open a filter (automation), change the hat pattern, add a fill in bar 8 (snare 16ths `....X...X.X.XXXX`, kick drop, reverse swell).
- Transitions: a 1-2 bar break before a drop (drums out, pad or stab alone, a riser: `preset:noise_riser` note held for the break). Cut the kick in the last bar before a new section.
- Intro and outro: 8-16 bars that DJs can mix; start with drums or a single pad, fade with master automation over 6-8 bars, not a cliff.
- Keep a buildup clear and bring the voice in on the drop. Vocal bits in the bars before a drop muddle the buildup
  and take attention from the drop itself.
- Hold the hook's payoff line until the story earns it. Sing the lead-in lines first, and give the payoff its first
  appearance where the song turns (after a refusal, a break, the point where the backing fills out).
- Where a phrase's halves land matters more than where they were sung. Moving a first half one beat earlier (its
  last word on beat 4, before the kick) and the second half one beat later, so every half ended on beat 4 into the
  1, was the take the listener chose from seven timings. Offer a few timings side by side, then check the pick in
  the whole song.
- Spoken lines: count the syllables and put the stressed one on the 1. In an intro, about one line a bar; after the
  drop a line can take its time.
- A long stretch with no voice is where a song drags (19 bars of one groove did). Fill it with what was already
  said, repeated and cut up on the beat, not with new material.
- A robot voice that reads high and female: a vocoder carrier on the root and fifth two to three octaves above the
  bass note, a nasal peak near 2.8 kHz, asymmetric grit, chorus on the synth side; later in the song add a fourth
  below and an octave above to fill it out.
- Verify at the end with `analyze_structure(source='render')`: the arrangement map rows should show your sections and your energy curve. If two sections look the same in the map, they will sound the same.
