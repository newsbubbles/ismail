# Instruments that sound real

The default failure: every part is a `synth` (basic waves, one filter, an envelope), so a cowboy song comes out like a 1990s game console playing a cowboy song. The `synth` type is right for synth sounds (basses, leads, pads, stabs in electronic music). An acoustic or electric instrument (guitar, strings, piano, brass, voice, a crowd) needs one of the routes below, and the choice goes in the Session Sheet with the example it is modeled on.

## 1. Get an example before you design anything

A sound is designed toward something you can measure. In this order:

1. **The user's example.** Ask: "is there a recording where this instrument sounds the way you want?" A reference song, a YouTube link, a file. Ask even when they did not mention one.
2. **A solo passage in the reference.** When recreating a song, find a few seconds where the instrument plays alone or on top (an intro, a break). `analyze_structure` and `spectrogram` show where. That chunk is the target; the rest of the song waits.
3. **A free recording of the instrument.** CC0 or permissive: Freesound (filter CC0), VSCO 2 Community Edition, University of Iowa Musical Instrument Samples, the tonejs-instruments set (its strings are VSCO 2 CE, CC0, and a Freesound cello pack by flcellogrl, CC BY 4.0: credit it). A few notes across the range at two dynamics is enough to measure. VSCO 2 CE file names are one octave low (a file named C4 sounds C5), the harp excepted: shift +12 when you import from it, and check one note's pitch first. Log it in the song's `ref/SOURCES.md` before measuring (title, link, recordist or player, licence, what you measure from it) and credit it in the voice's INFO.
4. **Memory, written down.** With nothing to measure, write a Sound Sheet from what you know of the instrument before coding: how it is excited (pluck, bow, breath, hammer, strike), what resonates (string, body, tube, membrane, room), which partials are strong, how the highs decay against the lows, what the attack sounds like, what noise rides along (bow hair, breath, fingers, fret buzz), how velocity changes the colour, and how two notes in a row differ. Then show it to the user; it is cheaper to correct a sheet than a voice.

Reference audio is for measuring. Never place it in the render.

## 2. Pick the route

| route | when | how |
|---|---|---|
| library voice or profile | `voices_list` has it (grand_piano, growl, sfx, electric, kit70, mimic profiles ...) | `voice_help(name)` / `instrument_help(type='mimic')`; set params, and for a performer add the rig `voice_help` lists; done |
| sampled library voice | a Rhodes (`rhodes`), a real acoustic kit (`rusty`), a clean electric guitar (`emily`): real multisamples played note by note | `samples_list` says whether its set is here; if not, tell the person its size and licence and `samples_fetch(<set>)` only on their yes (the Rhodes samples are CC BY-NC: free for their music, not for redistribution) |
| mimic | you have a few isolated recorded notes of the instrument (bowed, blown, sung work best) | `mimic_measure(name, folder=...)` (one note per file, named by pitch), read its leave-one-out report, then `{"type": "mimic", "profile": name}`; pass the open strings as `defaults` for bowed strings. A RECORDING WARNING means the take is faulty, not the instrument: re-record it (see below) |
| measured voice | the instrument needs a mechanism mimic lacks (touch harmonics, articulations picked by velocity, a crowd, foley) | write `<project>/voices/<name>.py` from the principles below, fit its params to the example |
| sampler | you have a clean recording of the exact sound and one pitch or a few pitches is enough (a hit, a stab, a vocal chop) | `sound_import`, then a `sampler` track (or a `kit` mapping pitches to samplers) |
| sprite (`synth`) | the sound is a synth | presets + `references/sound-design.md` |

The voices that sound best in this project (grand_piano, the bowed strings, the hum, the concert audience, the guitars) were all measured voices. mimic does the measuring for you when you have recordings; for a hand-written voice, start from the closest existing one: copy it into the song's `voices/` and change it, rather than starting from a blank file.

## 3. What makes a measured voice sound real

Each of these was the difference between "a synth imitating X" and X in at least one voice here:

- **Every partial has its own envelope.** Highs die faster than lows on a string (a gong is the reverse: highs outlast lows). One amplitude envelope over a static spectrum is the console sound.
- **Check the take before you measure it.** mimic_measure and sound_import warn on two faults that are otherwise learned as the instrument's sound. Peaks flattened near 0.89 to 0.90: the browser mic chain (the phone page, Chrome) or a limiter squashed them, so a "peak over 0.98" clip test never fires, and attacks and loudness measure soft; record quieter, or with the mic's processing off. Nothing above about 8 kHz (8-12 kHz more than 60 dB under 0.5-2 kHz, where a normal mic reads -26 to -30): a Bluetooth headset sends 16 kHz audio, and the synth then fills the empty top with hash; record with the phone's or computer's own mic, or pass `no_top=True` when the instrument truly has no top (the profile says so). Phone hums carry the same warnings as `capture`.
- **A body, measured, and not smoothed away.** Read the instrument's resonance curve from the example and let every partial read it at its current frequency, so vibrato makes harmonics flutter through the resonances (the bowed voice). Body resonances are narrow: which harmonic lands on one is what gives each note its own colour ("a colour specific to that note", in a listener's words). Smooth only as much as your data forces; with few notes spread wide, test the choice leave-one-out (mimic_measure does). A guitar body has modes near 100, 200 and 400 Hz; a pickup is a comb plus an LC resonance.
- **Vibrato that moves, and wanders.** Real vibrato changes every cycle: on a violin note, rate 4.5-6.6 Hz and depth 5-25 cents, building up over the first fraction of a second. Most of that change is a slow wander over about a second ("fast, then slightly slow, then fast again"); independent random cycles sound jittery, one steady sine sounds like a machine.
- **The note breathes.** A bowed or blown note swells and dips 2-4 dB over the seconds as bow pressure or breath changes. A flat level was heard as "no personality".
- **The attack is measured with short windows.** Analysis frames long enough to resolve harmonics (90-190 ms) swallow the rise and make every note start at full level ("hits the string" where the player eases in). Time the attack per band from ~20 ms windows and rise in an S-curve.
- **Weak harmonics are not the noise under them.** Taking the loudest bin near a weak harmonic reads the noise's peaks and overstates it by several dB: too many upper harmonics, heard as "brassy, like a trumpet". Subtract the local noise floor.
- **Sympathetic strings.** Open strings ring along when the played note shares their harmonics (a violin's open G and A kept ringing 3 s after the bow stopped). Listeners picked this as the closest lens on violin and cello.
- **Unison strings beat, starting in phase.** A piano note is 2-3 strings a hair apart: every strong partial swells and dips 0.3-2.3 times a second by 5-16 dB. Struck together, they start in phase and first cancel 1/(2 x beat rate) seconds later, the dip a listener hears as the note "fading out in the middle". Random starting phases fill that dip in; no beating at all sounds "like a horn".
- **Where it is excited.** A pluck or bow at 1/n of the string removes every n-th partial (pluck-position comb). A touch at the 1/node point is a natural harmonic: only multiples of the node survive (eharm).
- **Noise between the harmonics, calibrated by synthesis.** Bow hair, breath, finger and hammer noise, measured as the level between partials. Estimating it directly reads 10-20 dB low when the noise is uneven within a band (bow noise is); rebuild the note and raise the noise until the energy between the harmonics matches the recording. Missing air is heard as "not crisp, no breathiness". Without it a voice sounds like an organ.
- **Inharmonicity and stretch.** Stiff strings (piano, low guitar strings) run sharp up the series.
- **More than one of it.** Two string polarizations, 1-3 detuned strings per piano note, a section of detuned players each with their own vibrato and timing. Static unison detune is the cheap version.
- **Velocity changes colour, not just level.** Harder is brighter, with a faster attack and more noise; for guitars, very soft can mean a harmonic or a tap.
- **No two notes identical.** Seed small random variation per note (timing, level, brightness, noise). Repeated identical hits read as a machine.
- **The attack is its own sound.** The hammer knock, the pick, the chiff, the consonant. It is short and does much of the instrument's identity.
- **Space.** Real instruments are heard in a room: a spaced-pair stereo image (per-partial phase and level between channels), early reflections, a reverb send. A dry mono voice sounds like a chip. If every harmonic of a recorded note dies at the same rate after the bow stops, whatever its frequency, that is the recording's room, and a model of that recording needs it ("roomy within the first second").
- **Still open: the last "crispness".** After four rounds, listeners still hear a fleeting crisp top on real bowed strings that a harmonics-plus-noise model lacks. Measured: real high harmonics are smeared into narrow noise bands (only 20-50% of their energy on the line). Noise skirts around each harmonic reproduce that on a measured note but made unmeasured notes score worse, so they are an option (`smear`), not a default.

Build it as a voice module with an `INFO` dict (summary, range, velocity, params, source of the measurements), so it can move into the library later.

## 4. Match it chunk by chunk

Do not tune an instrument across a whole song. Take one short chunk where it is exposed (2-4 bars, often the intro) and loop on it:

1. `sound_extract` a clean note or event from the chunk, or `project_new` a small project whose reference is only that chunk.
2. Write the chunk's notes (from `notes_from_audio_loop` or by ear-reading `analyze_roll`), render only those bars.
3. Compare: `spectrogram` of both (a missing harmonic, a wrong decay, an absent noise band shows up there first), `analyze_timbre`, `sound_compare`, `instrument_fit` for the voice's params, `eq_match` for the long-term balance.
4. When a fit pins a parameter at the edge of its range, the model is missing a mechanism (harmonics, a second pickup, a body mode); add the mechanism, not a bigger range.
   - **Measure two pitches before reading a spectrum as a recipe.** A peak at the same Hz on two different notes is a fixed resonance (a filter, a body, a cab), not a harmonic level: a G-funk bass had one at ~196 Hz, and `instrument_fit` stuck at a wrong cutoff until the filter range was set around it. Stereo width plus a clear low pitch in the target means detuned unison.
   - **Fast "vibrato" may be beating.** A held note that wobbles fast can be beating against another part's overtone. Measure it where no other part shares the pitch class before building vibrato into the voice.
   - **Glides need the sprite.** mimic has no portamento yet, so a gliding synth lead or bass stays a sprite patch.
5. Play the chunk to the user (below). Only when they say it sounds like the instrument, move to the next chunk or the rest of the song.

Learned the hard way: a Polyphia recreation had the right notes on the guitar, but the guitarist plays most of them as harmonics. The transcription was "correct" and the part sounded wrong until the voice itself could play touch harmonics, and that only showed once the intro was matched on its own.

## 5. Ask the human, every time you play them something

You cannot hear; the user can. Every time you give them an mp3 (`render(mp3='also')`):

- Say what to listen for: "does the guitar in bars 1-4 sound like the one in the reference? Too clean, too dull, wrong attack, wrong body?" Ask about one or two things, not "what do you think?".
- Say what you measured, so they can tell you where the numbers and their ears disagree.
- Write their answer down in the song folder (`notes/feedback.md`: date, draft, what they said, what you changed). The ear wins over any metric: when it disagrees with a score, the score is missing something, and that is worth a line in the notes.
- **A/B one part at a time.** One mp3 per part: the reference part for 4 to 8 bars (its stem, retuned if the record is off pitch, band-limited to drop other parts sharing the stem), a 1 s gap, then yours, both at matched loudness: measure each (pyloudnorm, or ffmpeg `loudnorm` with `print_format=json`) and apply one fixed gain per clip (`volume=<dB>`), never `loudnorm` as the filter itself (see mastering.md). Drums, then bass, then lead. The user answers per part ("beat is good, bass very close, lead not following the melody"), which names the next fix exactly; one mix-level question does not.
- When a lesson holds across songs, it belongs in this skill (or the voice's INFO), not only in one song's notes.

**When the numbers plateau, give an eye exam.** Put the real sound next to "lenses", versions that each change exactly ONE named thing (body colour, wood ring, room, open strings, bow noise, brightness, attack, vibrato, evenness), all at the same loudness and long enough to include the release. Ask the user to pick the closest and say what is still off in those words ("E, but the vibrato starts slower, more air"). Host it as an exam page with a Submit button that writes the answers to a file you read
(`references/blind-tests.md`, "Page mechanics"). One round of this found three things no metric had shown (moving vibrato, missing air, sympathetic strings); the metrics were blind to them because they average over time and stop at the release. To know when you are done, follow it with a blind exam (`references/blind-tests.md`).

## Genre palettes

Before choosing sounds, list what the genre is actually played on, then check `voices_list` for each:

- country / cowboy: steel-string acoustic guitar, fiddle (bowed violin), upright bass (bowed contrabass, pizz), harmonica, pedal steel, brushed snare
- orchestral / film: bowed strings (section size, tremolo, ponticello), brass, timpani, choir, piano, gong
- folk / singer-songwriter: acoustic guitar, voice or hum, light percussion, room
- rock / metal: electric guitar through amp and cab, bass guitar, acoustic kit
- jazz: upright bass, piano, brushes, ride cymbal, horn

Anything on that list with no voice yet is a voice to build (route 2 or 3) before the arrangement, not a synth to settle for.
