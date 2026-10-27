# Mastering

Mastering is its own pass, after the mix is right and before anything goes to the user as "the song". A render with only the default safety limiter is a mix, not a master. Most of the difference between "demo" and "record" at this stage is loudness that fits the genre, a balanced spectrum, and glue. Which limiter number belongs to which stage (mixing, mastering, a live set, a deck, a stream) is in `loudness.md`.

## 0. Fix the mix first

Mastering cannot fix a masked kick, a harsh lead or a clipping track. With a reference, the balance lines of `render(stems=True)` (each stem's share of the mix per 8-bar stretch against the reference's) come first: no stem 3 dB off in a loud stretch, no layer missing. Before touching the master: every track peak under 0 dBFS, limiter gain reduction under about 6 dB with the mix as it stands, and the Listening Report clean. If the limiter is already working hard, turn tracks down; do not start mastering on top of it.

## 1. Pick the target from a reference

- If the song has a reference, read its loudness: `analyze_overview(source='ref')` prints integrated LUFS and peak. Aim within 1 dB of it.
- Without one, by genre (integrated): solo piano, classical, ambient -18 to -16 LUFS (keep the dynamics); acoustic, folk, singer-songwriter -14 to -12; pop, rock, hip-hop -11 to -9; EDM, dubstep, drum and bass -9 to -6.
- Psytrance (full-on, a measured record): the grooves sit at -9.4 to -10.7 dB rms and the breakdowns at -16.9 to -18.3, so the drop from groove to breakdown is about 7-8 dB and should stay there. A version mastered to -8.5 LUFS integrated matched it.
- Streaming services turn loud masters down to about -14 LUFS. Louder than the genre needs only costs punch.

## 2. The master chain

On `track='master'`, in this order:

1. **eq**: gentle, broad moves only (1-3 dB): a low cut around 25-30 Hz, a small dip where the mix builds up (often 200-400 Hz), air on top only if the reference has it. For a reference, compare long-term spectra (`analyze_spectrum` on `render` and `ref`) over the same section; `eq_match` does this per track.
2. **compressor** (glue): ratio 1.5-2, attack 20-30 ms (lets transients through), release 100-200 ms, threshold so gain reduction is 1-3 dB on the loud sections. More than that and the drums flatten.
3. **width**: `mono_below_hz` 120-150 keeps the sub mono (it collapses to mono on club systems and phones anyway); width above that 1.0-1.2 at most.
4. **limiter** last: raise its `gain_db` until integrated loudness reaches the target, gain reduction under 3-4 dB on the loudest bars. For anything that becomes an mp3, set `true_peak: true` and ceiling -1.5 dB: a plain limiter holds sample peaks only, so bright material goes 1-1.4 dB over its ceiling between samples, and a 320k mp3 adds about 0.4 dB more. When re-mastering material that is already mastered, put a soft clip (`distortion` mode tanh, low `drive_db`) ahead of the limiter instead of asking the limiter for all of it.

## 3. Check it

- `render(mp3='also')` and read: integrated LUFS vs target, peak, limiter gain reduction.
- `analyze_overview(source='render')`: the quietest sections should still be audible (a -40 dB section in a -12 LUFS song is a mistake unless it is a deliberate breakdown).
- Loud sections against quiet ones: `analyze_sections` with the Sheet's form map (or `analyze_bars` level per bar). Mastering should keep the contrast the arrangement planned (the energy curve in the Session Sheet), not flatten it. An orchestral piece with a pianissimo opening and a fortissimo climax measured 22 dB between them and the listener heard it as dynamic; the fix that mattered was the build before the climax, which sat within 0.2 dB of it until its faders came down 4 dB.
- A live set needs a loudness target too, and its master is the live safety chain (a trim, a cap on sustained loudness, a limiter, ceiling -1 dBFS), not a mastering chain. Start it with `live_start(..., trim_db=4, cap_db=-12)` for about -16 LUFS. Left out, the engine takes the `live.trim_db` and `live.cap_db` settings (`config_get(group='live')`), else -6 and -16, about -22 LUFS: a live set at -21 LUFS was heard as "nothing". Then balance with faders until the rider and limiter in `live_status` read near 0 dB (live.md, Rules). `render` prints QUIET under -20 LUFS for a song.
- Compare against the reference at matched loudness. A louder render always seems better, to people and to perceptual scores.
- Match loudness with one fixed gain: measure (pyloudnorm, or ffmpeg `loudnorm` with `print_format=json`), then
  `volume=<dB>`, the smaller of what reaches the target and what keeps the true peak under the ceiling. Never apply
  ffmpeg `loudnorm` as the gain: single-pass is a dynamic AGC, and two-pass with `linear=true` silently falls back to
  it whenever the linear gain would break the true-peak ceiling. It rides the level (+6 to +11 dB inside one bed, the
  first half second loudest), which the person heard as "the air and the good sound for a split second, then worse".
- Then ask the user to listen on two systems if they can (headphones and a phone speaker): "does the low end hold up on the phone? is anything harsh on headphones?"
