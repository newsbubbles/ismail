# Sound design in ismail

For acoustic and electric instruments (guitar, strings, piano, brass, voice, crowd) read `references/instruments.md` first: this file is mostly about sprite (the `synth` type) and drum-synth patches.

`instrument_help(type)` and `fx_help()` list every parameter with defaults. Start from a preset (`presets_list`) and change three or four things; a full patch from scratch is rarely better.

## Voices: instruments written as code

`voices_list` first: a built-in voice (grand_piano, growl, sfx ...) may already be the sound you need, and `voice_help(name)` shows what its velocity does. When you engineer an instrument as code that the song will reuse, write it as a voice module in `<project>/voices/<name>.py` (the engine looks in the project's `voices/` and in
`$ISMAIL_VOICES`; a song that keeps its voices in `songs/<slug>/voices/` has `build.py` copy them into
`proj/voices/`) (a `voice(freq, t, vel, gate, sr)` function plus an `INFO` dict), not as a `code` string in the track: the project stays portable, edits re-render automatically, and the voice can later move into the library. Never paste absolute paths or `sys.path` hacks into a `code` instrument.

A **performer** voice plays a whole part: `perform(notes, total_n, sr, bpm=120, lanes=None, beat0=0.0, **params)` and no `voice()`. Take `beat0` (the song beat at sample 0) and draw each note's randomness (humanize, pick, drift, noise) from a generator keyed on the note's song beat and pitch, as `voices/guitar/electric.py` `_rng` does, never from one generator drawn note after note: a render of any slice of the part (a bar range, the live engine's bar chunks) then plays exactly as the whole part does. Noise that runs under the whole part (a snare's wires) is drawn per block of the song's own timeline (`kit70._wire_noise`). A performer that names `project_root` in its signature also gets the project folder, to find files the person keeps in the project's `ref/` (`toms` looks there for its samples); never write an absolute path into a voice.

A voice renders the same in the whole song, in a bar range and live only if it follows the five window rules in
`windows.md`: seed by song time, never index notes by their place in the list, a held note arrives cut, no work
that depends on the buffer's length, and a window's last bar is not the song. It also says how ismail builds a
window in the studio, on a deck and live.

## Gain staging

One synth voice peaks near -9 dBFS and one drum hit near -6 dBFS at full velocity, so faders at 0 dB are a sane start. Chords of 4 notes with unison add up: pads usually sit at -8 to -12 dB. Check `render` output: every track peak under 0 dBFS, master limiter gain reduction under ~6 dB while mixing (the number for each stage is in `loudness.md`). A limiter pulling 10+ dB means your faders are too hot; turn tracks down, not the limiter.

## Recipes by role

| role | start | key parameters |
|---|---|---|
| kick | `{"type": "kick"}` | pitch_end = key root around 38-55 Hz, pitch_start 90-160, pitch_decay 0.02-0.05, decay 0.25-0.45, click 0.1-0.4, drive 3-6 dB |
| snare | `{"type": "snare"}` | tone_hz 130-200 (the body), tone_mix 0.35-0.55, noise_hp 300-2000, noise_decay 0.15-0.35 |
| hat | `{"type": "hat"}` | decay 0.03 closed / 0.2 open, hp 1.5-8 kHz, metal 0.5-0.9; add `chorus` or `tremolo mode=pan` for width |
| bass | saw + sine sub at octave -1 | lp24 400-1600 Hz, mono true, glide 0.02; fx `{"type": "eq", "bands": [{"type": "lowcut", "freq": 40}]}`; `duck` source=kick depth -6 to -12 dB |
| stabs / riff | saw unison 3-4, detune 10-25 cents | lp24 1-3 kHz, filter env_amount 0.5-1.5 oct (4 oct makes every note a bright click), amp s 0.5-0.8 so gaps don't open |
| pad | saw unison 3-5 + noise 0.1-0.3 | lp 1.5-3 kHz, amp a 0.005-0.6, s 0.3-0.8, chorus + reverb send -8 dB |
| lead | square pw 0.3 + saw | lp 3-6 kHz with env, delay 0.75 beats pingpong, reverb send |
| voice (sung vowel) | saw + pulse + noise 0.04 | fx `formant` vowel '@' / 'e' / 'a' (see analyze_formants), vibrato lfo pitch 5 Hz depth 0.1 delay 0.3 |
| sung voice, in tune | the take as an audio clip on its own track | `autotune` fx on that track: `key` + `scale`, or `notes_from` = the melody track it should follow; `speed_ms` 0 for the hard robotic sound, 20 to 60 firm, 100 and up keeps vibrato (a pitch curve made by rules is what people hear as "autotune"); `formant` moves the voice's character without its pitch. A take outside a song: `sound_autotune(path, ...)` |
| voice (words) | `sound_speak(text)` on a muted track as audio clips | `vocoder` fx on a synth track, modulator = that track; speech is short, so hold vowels with a formant voice |

Drum synths are mono; width comes from effects. Sidechain feel: `duck` with `source='kick'` is deterministic and cheap; `compressor` with `sidechain` reacts to the kick's audio.

## Designing against a target sound

1. Get a clean target: `sound_extract(name, source, bars, step, every=, length_sec=)` averages every occurrence of an event at one grid position. Consistency near 1 means an identical sample each time; below 0.5 means the event is buried or varies (pick other bars or steps).
2. `instrument_fit(target='sound:<name>', instrument=..., params={path: [lo, hi]}, notes=<what the target plays>, fx=[...], fmin=, fmax=)`. It fits spectrum, envelope, stereo width and pitch clarity. Fx parameters fit too (`fx.0.depth_db`). Use `fmin` to ignore bleed (for example kick sub under a snare).
3. Read the comparison it prints: `<<` bands are where you are too quiet. A fit that pins a parameter at a range edge is telling you the range is wrong or the architecture is missing something (the pad fit pinning noise at max meant the target has an air layer).
4. Apply, then check the sound in the song, not alone.

## Tuning a track in the mix

`track_fit(track, params, bars)` renders the track with the rest of its stem group over a window and scores it against the reference stem (perceptual + bands). Use it only where the reference stem is clean (an intro with one instrument, for example). Pin `volume_db` unless level is the question: the learned metric can be bought with loudness.

## Effects order that works

instrument -> eq (cleanup) -> distortion -> filter -> duck/compressor -> chorus -> delay; reverb on a bus with sends. Master: limiter last, ceiling -1.0 dB for anything that becomes an mp3 (`references/mastering.md`). A drum bus (tracks output to a bus with compressor attack ~8 ms, ratio 3, plus a short room reverb mix 0.1) glues drums; fast attack plus clipping made them worse in testing.

A sung take: `autotune` first, on the dry take, then eq, compressor, delay and reverb. It reads the voice's pitch,
and an echo or a reverb tail in front of it gives it two pitches at once, so the correction wanders. A doubled or
harmonised voice is a second track with its own `autotune`, not a second one in the same chain. In a live set the
take is tuned as a file first, with `sound_autotune` (`live.md`, "Sung takes, tuned").

## Guitars and other played strings

Learned matching a 1970 guitar record until 20 of 22 single notes passed a blind exam (`references/blind-tests.md`).

- Start from the library: `voice_help(name='electric')` (guitar or bass) and `voice_help(name='kit70')` list presets and the rig each was fitted with. The voice is the DI signal; without its rig it sounds like a synth. Presets: `strat70_lead`, `strat70_rhythm`, `strat70_rotary`, `pbass70`, `kit70` (`track_add(..., instrument='preset:strat70_lead')`), then `fx_add` the rig's effects in order.
- The rig effects are `wah`, `fuzz`, `univibe`, `amp`, `cab`, `rotary`, `tape`; `fx_help(type='amp')` documents each. They play live as in the studio.
- `electric` is tuned a half step down by default (guitar Eb2-Eb6, bass Eb1-G3), as on the record it was fitted to; set `tuning` (MIDI notes of the open strings, low to high) for E standard. `kit70` plays General MIDI drum numbers (36 kick, 38 snare, 42/44/46 hats, 49/57 crashes, 51 ride; `voice_help` has the full map).
- Rig order: wah -> fuzz -> amp -> cab -> delay -> eq for a lead (wah before the fuzz, as on the record); univibe before the amp; a rotary speaker after the cab. Amp noise (`hiss_db`, `hum_db`) belongs in a lead: "too clean" was the most common tell.
- **Put the parts in the record's register before fitting any tone.** Measure the pitch percentiles of each reference part and move voicings and the bass line there first. Two tone fits failed until the notes moved; register masquerades as tone.
- **Fit the harmonic profile, not the long-term spectrum.** A long-term spectrum mostly measures which notes were played. The level of each harmonic relative to the fundamental, averaged over notes, is note-independent: fitting it took a bass from 8.6 to 4.6 dB off.
- **Separated stems are dark above about 1.5 kHz.** A correction measured on the stem cut the top while the user heard "upper harmonics too muted". When a stem-based timbre number and the ear disagree, the ear wins.
- **Fitting a rig fast:** grid the nonlinear settings (gain, fuzz, drive), fit the linear stages (tone stack, cab, eq) on the spectral envelope for each grid point, then polish the best few with real renders. Minutes, where one global fit over every knob took hours.
- A fit that pins at a range edge is a missing mechanism (a pickup's tone-knob resonance, a velocity-sensing pickup, treble that dies faster than the fundamental), not a range to widen.
