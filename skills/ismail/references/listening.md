# Listening with ismail (you have no ears; these are your ears)

## Which tool answers which question

| question | tool | read it like |
|---|---|---|
| Did it render, is anything silent or clipping? | `render` output | per-track peak/rms, `SILENT`, `CLIPPING`, limiter gain reduction |
| Is the song shaped the way I planned? | `analyze_structure(source='render')` | one char per bar per row: 9 loudest, each step -4 dB, '.' silent; `root` row = bass note per bar; sections lettered by similar arrangement |
| Are the notes what I meant? | `notes_read(view='roll')`, then `analyze_pitches(source='track:x')` | the roll shows what you wrote; pitches shows what actually sounds |
| Is the drum pattern right? | `analyze_drums(source='track:drums')` | step strings per lane; X within 4 dB of the lane's loud hits |
| What pieces does a kit have? | `analyze_kit(source='ref:drums')` | one component per piece: energy share, pitch region, hits per 16th; `(fragment)` = lower k |
| How much swing? | `analyze_swing(source=)` | 16ths: the 'a' against the 'and', in beats and ms; straight = 0 |
| Does the dynamic shape work? | `analyze_sections(sections={...})` | rms, peak, loudest and quietest 400 ms per section; WARNING when a build is as loud as its climax |
| Does it pump / gate / groove? | `analyze_envelope(bars, band=)` | digits per 16th; a kick-ducked part shows dips on the beats |
| Is the low end clean? | `analyze_bars` sub and bass columns, `analyze_spectrum(span=)` | kick and bass should not both peak in the same 1/3-octave band |
| What is this sound? | `analyze_timbre`, `analyze_spectrum`, `sound_compare` | harmonic slope, brightness and rolloff, envelope times, width |
| What is the voice saying? | `analyze_formants` | F1/F2 per half beat, nearest vowel |
| Why does it sound fake or digital? | `spectrogram` of the reference and of yours, same bars | look at them together, one above the other: "chopped rectangles vs ringing lines" showed partials that stopped instead of decaying. Then an ear test (`references/blind-tests.md`) |
| Anything else | `spectrogram` (PNG) | it caught a missing low-pass that no text view showed |

Zoom in for eyes. `spectrogram(seconds=[t0, t1], f_lo=, f_hi=, ruler=True, words=[...])` draws one sound (a word,
a hit, a note plus 60 ms either side) on a fixed plot box, so the reference's picture and yours line up pixel for
pixel; the ruler counts ms from the window start and each word's start is drawn and named. A `.json` beside the PNG
maps a pixel to (s, Hz): `analysis.eye_address(view, fx, fy)` gives the spot the way you and the person both name it
(`0.19 s 7.9 kHz "this" hiss`; bands body 0-1 kHz, vowel bands 1-4, hiss 4-8, air 8-16), so a screenshot they mark
points you to the same place. From the Voice agent's work, where the eye found in one look what 40 rounds of numbers
missed.

## When to zoom

The zoom is a measurement, not only a picture for exams: the sound becomes a large image of time against
frequency, and you frame any part of it at the resolution the question needs. Reach for it early when:

- **the person names a texture**: robotic, digital, bitcrushed, smeared, gargle, "old TTS", cut off, pre-delay,
  reversed, phaser, plumes, motion blur. These live inside one moment, where long-window numbers cannot see;
- **the numbers say close and the ear says no**: spectra within 1 to 2 dB while the person heard "30% there", or
  every sensor at its best while they still heard "digital";
- **the sound is hard**: a voice, sung words, a played instrument (guitar, piano, strings, brass), anything whose
  realness is inside each note (attack, how partials decay, noise, pitch motion). Synths, drums and arrangement
  usually go fine on the text views;
- **two rounds passed without progress**: rotate the sense that measures (ears and numbers to eyes);
- **after a fix aimed at a spot**: zoom the same spot again and see that the difference faded.

How: draw the reference and yours on the same window and read them one above the other. Zoom two ways: a few ms
around an edge (a time-sharp view shows a start early or late) and a whole word or note (a frequency-sharp view shows
texture up high). For a blind check of your own, `exam_eye_crops` then `exam_eye_score`: if you can pick the real
one from the picture, the person will most likely hear the difference too. A diff of two zooms (lighting only what
lies outside the reference's own take-to-take spread) is coming from the Voice agent's work.

What they said, and what the zoom found (the Voice agent's rounds):

| they said | zoom | what it was |
|---|---|---|
| "almost sounds reversed", "pre-delay" | 2.5 ms on the first word | the synth was loud at the start of a sound where the voice is near silent: its loudness smoothing was too coarse |
| "cut off the D" | the word "code" | the voice keeps buzzing through a closed consonant; the synth went silent there |
| "ate the is" | the word "is" | its hiss started 50 to 200 ms early in 14 of 29 takes |
| "like smoke plumes" (the voice), smooth (ours) | frequency-sharp, above 4 kHz | the synth's top end was too even |
| "old TTS", "a line between words" | each word, full band | the voice's formant bands slide and curve through a sound; the synth's stepped as flat blocks |

Words for pointing at a spot, so you and the person mean the same place: bands **body** 0-1 kHz, **vowel bands** 1-4,
**hiss** 4-8, **air** 8-16; shapes **stripes** (one line per voice pulse), **bars** (level bands across a vowel),
**plumes** (smoky, uneven, changing), **dashes** (short level lines at one height), **pockets** (dark gaps), **edges**
(where a sound starts), **tails** (where it fades), **ridges** (fine lines that stay sharp); movement **blur along
time**, **blur along pitch**.

Tracks are analysable as `track:<name>` only after `render(stems=True)`. A track stem is the track after its own effects and fader, scaled by the master chain's gain, so the stems sum to the mix. That means a sidechain duck shows up in `track:<name>`; if it barely dips, the duck depth is small, not the stem pre-fx.

Readings that mislead:
- Long-window numbers are blind to fakeness. 8-bar spectral envelopes within 1.8 dB and stem balance within 0.7 dB while the user heard "30% there": what makes a sound fake lives inside single notes (attack, how each partial decays, pitch movement, noise). Report those numbers, never as proof that a sound is convincing.
- `analyze_drums` on a full mix (`render`, `ref`) is band activity: leads and pads show up as snare and hat hits. Read your drums as `track:<drum track>`.
- Lengths added up from typical (median) measurements come out short. Durations lean long (a few notes or words
  last much longer than the rest), so a sum of medians ran about 28% under real phrases (344 spoken sentences);
  phrase ends also lengthen, and pauses go missing. Fit lengths on a log scale, lengthen phrase ends, put the pauses
  back: the error on new sentences went from 28% to about 8% (the Voice agent's work).
- Level and band charts cannot see phase. A 3 ms all-pass in the high band moved no level in any chart and was heard
  at once as a "phaser". A stage that changes timing or phase but not level gets a blind pair before it is kept
  (`references/blind-tests.md`).
- One render is not a result. Random variation (seeds) moves the measures: a transcription score alone moved 3 to 7
  points between seed sets. Compare two versions on at least two seed sets before claiming one is better.
- `analyze_melody` on a track with delay or vibrato splits held notes into runs of short notes. Check pitch with it and rhythm with `notes_read(view='roll')`, or read the melody before adding the delay.

## Listening Report (write one after every render you judge)

```
Render: bars <a-b>, <LUFS> LUFS, peak <dB>, limiter GR <dB>
Levels:    <loudest track> / <quietest audible track>; nothing SILENT that should play
Low end:   kick peak band <Hz> vs bass peak band <Hz>  (separate, or one ducks the other)
Clutter:   notes/bar in busiest part = <n>; parts active at once = <n>  (more than 4-5 sustained parts = mud)
Rhythm:    analyze_drums / analyze_envelope on bar <n> matches the Sheet: yes/no (quote the line)
Harmony:   analyze_pitches bar <n> = <notes> (the chord you meant? yes/no)
Space:     width (analyze_timbre stereo line) <value>; reverb/delay sends present on <tracks>
Form:      analyze_structure rows match the form map: yes/no (which section differs)
Dynamics:  analyze_sections: range <dB> quietest to loudest; the section before the climax <dB> under it (3+)
Worst:     <the single worst line above> -> next fix
```

## Common readings and fixes

- Master limiter GR > 6 dB: pull the loudest tracks down 3-6 dB; re-render.
- `analyze_structure` shows a flat map (all 8s and 9s): no energy curve; drop parts in the intro and break, automate a filter.
- Level digits with holes where the part should sustain: amp sustain too low or notes too short (`notes_transform legato=true`).
- Bright wash across 3-10 kHz in a synth group: filter envelope amount too high, drive too high, or noise layers; darker is usually closer to finished records than it feels.
- A part that `analyze_pitches` reports with many neighbours at similar level (A2 with G#2 and A#2): heavy detune or distortion smearing the pitch; reduce detune or drive if the note should read clearly.
