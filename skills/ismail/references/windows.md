# Windows: a part of the song that plays like the whole song

A window is any stretch of a song rendered or played on its own: `render(bars=[a, b])`, a section loaded on a deck
(`live_load(bars=)`), the bars the live engine renders a performer in, the bars `live_parity` compares. A sound
designer writing a voice, a DJ writing a performer and a dev preparing a voice for ismail all need the same facts:
how ismail builds a window, and what a voice must do so the window sounds like the same bars inside the whole song.

## How ismail builds a window

- **A studio render of some bars** starts 8 beats before the window, so tails and sidechains settle, and takes notes
  that began a little earlier so they ring in. A note that began before the window with at least a beat left (a
  drone, a pad, a held string) comes in at the window's first sample with what is left of it.
- **A deck** takes the window's notes. A note still sounding at the window's first beat with at least a beat left
  comes in on that beat, as in a studio render of those bars. A performer voice gets the 8 beats before the window as
  context, and its held notes keep their real start.
- **Live, a performer** renders a bar at a time, each bar with the second of the part before it as context, cut and
  crossfaded at the bar line; a looping clip takes its context from the previous pass.

## The five rules for a voice

A voice renders the same in the whole song, in a bar range and live only if it follows five rules (found joining
windowed renders of a 146-bar song):

1. **Seed by song time**, never by where a note sits in the buffer: `beat0` plus the note's start, rounded once
   (`round((st + beat0 * 60 / bpm) * 1000)`, song milliseconds), not two rounded sample counts added.
2. **Never index notes by their place in the list**: a window gets only its own notes and a few before. Carry the
   identity in the note.
3. **A held note arrives cut**: a note that began before the window comes in at the window's first sample with
   what is left of it. A song-long bed generates by absolute time (noise per frame seeded by the frame's song
   number, fades over the song's first seconds), not by the note.
4. **Length-dependent work changes the sound**: a voice that draws noise for the whole buffer or filters it
   zero-phase sounds different when it is given a different length. ismail hands code voices each note whole; a
   performer still gets the window, so keep its draws per note and its filters causal.
5. **A window's last bar is not the song**: notes after it are absent and it fades out; compare windows at a join,
   not over their last bar.

Studio renders of songs using `electric` or `kit70` made before their seeds were keyed (late September 2026) are a
different take of the same humanization: a blind exam's old clips cannot be rendered again exactly.

## Checking a window

- `live_parity(song, bars=[a, b])` renders the bars in the studio, plays them on a silent deck and compares each
  track, bus and the mix from the window's second bar. The first bar is the window's edge: the studio rings in what
  came before, a deck starts clean. Known gaps it shows: a mono synth's glide between phrases, a drone that began
  long before the window (it comes in with a fresh attack), a track with no notes in the window (its tail and hiss
  from earlier bars are not played).
- The engine's own proof: `tests/test_live_parity.py` holds every live effect to its studio twin over a whole window,
  and `tests/test_live_song_parity.py` plays every instrument type and library voice on a deck against the studio
  (`development.md`, "Studio and live parity").
