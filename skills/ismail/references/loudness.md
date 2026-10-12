# Loudness: every output, and which number is for which stage

Everything the person hears goes through a master stage, a live set included, and its loudness is read at the
output. ismail has several limiters, and the skill gives a number for each of them. The numbers do not disagree:
each one belongs to one stage. Read this when a limiter reading and a rule seem to clash, and before you set the
level of anything that is not a plain render (a live set, a deck, a stream, a film's music).

## The limiter numbers, by stage

| stage | number | what it means | where you read it |
|---|---|---|---|
| Planning (the Session Sheet's mix targets) | gain reduction under 4 dB | the aim you write down before the first note | `render`: `master fx <n>: max gain reduction` |
| Mixing, before any mastering | gain reduction under about 6 dB | more means the faders are too hot: turn tracks down, not the limiter | `render`; "master limiter: idle" means under 0.5 dB |
| Mastering, the final chain | gain reduction under 3 to 4 dB on the loudest bars, ceiling -1.0 dB | after the limiter's `gain_db` has raised the song to its target; -1.0 because mp3 encoding overshoots a -0.3 ceiling | `render(mp3='also')` |
| A new project's master limiter | ceiling -0.3 dB, gain 0 dB | the default that catches peaks in a mix (a sketch starts at -1.0); set -1.0 before the master becomes an mp3 | `fx_help(type='limiter')` (the defaults) |
| A live set, its safety chain | limiter and rider at 0 dB | the safety chain is a floor, not a mixer: balance with faders | `live_status`, the `safety:` line |
| A live set, while it plays | past about 3 dB | layers add up: pull faders when the safety limiter reads more than this | `live_status`, after every addition |
| The live output | trim -6 dB, cap -16 dBFS rms, ceiling -1 dBFS | the engine's defaults, about -21 to -23 LUFS: quiet; a set raises them before `live_start` (`live.md`, Rules) | `live_status`, the `safety:` line |

Two more outputs carry a master stage of their own:

- **A deck** runs the loaded song's own master chain on the deck's sum (its compressor and limiter, as in the studio
  render), and the set's safety chain after it. Do not add a limiter on a deck for a mastered song.
- **A stream** (`live_stream`, and `stage_stream` on the stage) of a bus or a deck passes its own safety limiter, with
  the same trim, cap and ceiling as the live output. The master stream is the live output itself.

A render of the whole song prints QUIET under -20 LUFS: at a low playback volume that reads as nothing. Loudness
targets by genre or reference, and the master chain, are in `mastering.md`.

## How each output reaches its level

- **A song**: faders first (the mixing number above), then the master chain in `mastering.md`, read at `render`.
- **A live set**: faders against the safety chain, with the engine started at set loudness (`live.md`, Rules, and
  the DJ kit's `Set.start`). There are no master-bus effects on your own live tracks yet.
- **A song on a deck**: it arrives mastered; match it to the set with the deck's fader.
- **A film or any bed made from the master**: the master as it is. When it must sit at another level, measure it and
  apply one fixed gain, never a loudness normalizer (`mastering.md`, "Check it").
