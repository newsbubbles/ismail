# Ear tests: the eye exam and the blind exam

You cannot hear. Your metrics average over time, and they stop being useful exactly where "sounds fake" lives: inside single notes (the attack, how each partial decays, pitch movement, noise, micro-dynamics, timing). The user's ear is the only instrument that catches those things, so it has to be put to work efficiently. An ear test is a small local HTML page of clips built so that each answer is a measurement you can act on. Treat building it as sound engineering, not as a chore: in a guitar study the test page moved the result further than any fit.

Exams are how the person's senses enter the work. Their answers are the data no metric gives: the eye exam turns
"something is off" into a direction in their words, the blind exam says when you are done, and the words they use
go into the lexicon (`lexicon_note`), so later rounds speak their language and their ear for the craft grows with
yours.

There are two kinds of test. Use both, in this order.

| | eye exam (tuning) | blind exam (verification) |
|---|---|---|
| question | which direction is closer? | can the user tell mine from the real one? |
| clips | the real sound, plus lenses that each change ONE named thing | R (real), A and B (mine), labels hidden and shuffled |
| answer | "C, but the vibrato starts slower, more air" | per trial: which is real, or "can't tell", plus a note on the tell |
| use when | the numbers plateau and you need a direction | you think you are close and need to know if you are done |
| score | the words the user uses | the share of trials the user could not decide |

## 0. Before any exam reaches the person: `exam_check`

Run `exam_check` on every exam before you show it, and show it only on READY: `exam_check(page='<the page or its
URL>', key={'<clip label or file>': '<its class>'}, secrets=['<source names>'], submit_url='<the page Submit>',
answers_path='<where you read answers>')`. It fails a page whose clips are missing or do not decode, whose loudness
spreads more than 1 LU, whose answer can be read from anything but the sound (file names, URLs, metadata, the page
source or a key file it loads, formats, lengths or order that follow the key), or whose Submit lands somewhere you
don't read. `phone_exam` runs it itself. A WARN is a likely tell: fix it, or say why it can't be fixed. The round trip
leaves one answer line marked `preflight`: skip it when scoring.

### The blind crop check: can you tell from the picture?

Before a real-against-made round, look first (Nate's "90 degree rotation": when one sense plateaus, rotate which
one measures; a spectrogram is an image an eye reads well). `exam_eye_crops(pairs=[[real, made], ...], out=...)`
cuts the same window from both clips of each pair (a word plus 60 ms either side, when you pass `windows`) and puts
them side by side as "1" and "2" in a random order, with the key in a file you do not open. Look at each crop, pick
the side that looks real, and score it with `exam_eye_score(out, {1: '2', ...})`. If you beat chance (it says NOT
READY), the picture gives it away and the person will most likely hear it too: find what you saw and fix that
first. Voice picked 15 of 16 on vox:r44-r45 (chance is about 3 in 10,000) before the person's ear did.

Two habits make the crops useful. Zoom two ways: a time-sharp view (short frames) shows a boundary early or late
(the "is" in vox ended 50 to 200 ms early in 14 of 29 takes); a frequency-sharp view (long frames) shows the
texture of the top end (the synth was smooth above 4 kHz where the voice looked "like smoke plumes"). And after the
person answers, show them the spectrograms (a reveal on the page), with their marks: their eye on the picture finds
the next measurement faster than any statistic.

### On the phone

When the person is away from the desk, `phone_exam(title, clips, question, chips, choices, key=, secrets=)` puts the
exam on the phone page: it runs `exam_check` first and refuses one that is not READY, plays each clip on its own
(the set's stream pauses and comes back after), and writes the answers where you read them. Say what to listen on
(their earbuds, a speaker) and record it with the answers, as for any device. Keep clips short and the questions
to two or three: they answer standing, walking, or between other things.

**A floor under every phone round.** A "can't tell" on a phone means nothing until you know what that phone and
ear can tell apart at all. `phone_exam(..., floor='auto')` adds floor pairs: the real clip against a 64 or 128 kb/s
MP3 copy of itself, or against itself, at seeded places (never first). The full set (64k, 128k, identical) goes out
on the first floor round, then one rotating pair per round; pass `floor='full'` again for a new listening device or
when a floor answer differs from the first. Each floor pair costs a slot of the 5 to 6 pair budget. The pairs are
renumbered around them, and the reply and `<answers_path>.floor.json` map the labels back and say which side is the
MP3. Read the floor first: if they pick the MP3 side as "different" more often than chance, the device and ear
separate even that, and a real pair they could not tell is a strong pass; if they never can, a "can't tell" says
little. From the Voice and Paper agents' work (ledger:M173).

### Record the devices, every round

The microphone and the listening device change what an exam measures, so the page records both (vox:r39 was lost:
the takes went through Bluetooth earbuds' microphone, which records 16 kHz audio, and nobody knew). On a page that
records takes, store the microphone's name with each take: after `getUserMedia`, `stream.getAudioTracks()[0].label`.
On an answer page, prefill the listening device and let the person confirm it: in Chrome on a computer,
`enumerateDevices()` lists `audiooutput` devices once the page has microphone permission (the `default` entry names
the current output); Firefox lists none but offers `selectAudioOutput()`; phones rarely name it, so ask (earbuds,
headphones, phone speaker, speaker). Listen for `devicechange`: earbuds connect mid-round. `phone_exam` does this
itself (a "Listening on" row, remembered, with what the browser can name). `exam_check` fails classes whose top
end differs (a band-limited mic on one side) and warns on any clip with nothing above 8 kHz.

## 1. The eye exam: lenses that change one thing

Put the real sound next to lenses, versions that each change exactly ONE named, physical thing (body colour, wood ring, room, open strings, pick or bow noise, brightness, attack, vibrato, evenness). The names become the user's vocabulary for the rest of the session: "B, but more air" is an instruction you can execute. Loudness-match every clip and make clips long enough to include the release. One round of this on a bowed voice found three things no metric showed (moving vibrato, missing air, sympathetic strings).

**The picture round: let their eyes compare.** When the ear says "something is off" and the words run out, put
pictures in front of them. `exam_picture_round(out, items, title)` builds a page of cards, one sound each: the real
spectrogram and ours of the same window, stacked so one shows at a time. F flips, holding Shift peeks, B blinks at a
chosen speed, and the eye reads any difference as movement (the blink comparator). They answer about the picture
showing (M real, S ours, 0 can't tell) and pin comments at a time and frequency, a click for a point or a drag for a
box, each saved with its word. The key is served only after they submit, and `exam_picture_score(out)` lists every
card and every pin on the REAL picture or OURS. In the Voice agent's work the pins named what 40 rounds of numbers
missed ("fire plumes between the columns, ours are straight smears"). The page server is `python -m
ismail.exampage serve <rounds folder>` (127.0.0.1:8871; the op starts it), with byte ranges so long audio seeks.

## 2. The blind exam: can the user tell?

Per trial, three clips of the same moment: **R** the recording, **A** my instrument with the recording's exact expression copied onto it (its pitch curve, filter or wah curve, level curve), **B** my instrument played by my player model (my own vibrato, bends, dynamics, effect moves). The two lenses separate the two questions: A failing means the instrument or rig is wrong; A passing while B fails means the performance is wrong. Hide which is which, shuffle per trial, and reveal the answers with scores and spectrogram strips only after the user submits.

In a guitar study the blind exam went from 1 of 16 undetected to 20 of 22 in four rounds (1/16, 3/28, 14/24, 20/22). Each round's misses named the next fix. The design rules below each cost a round when they were missing.

**Climb from the smallest unit.** Start where a miss can be fixed: one note or gesture, then a phrase, then the part
in the band, then the full mix. The guitar study's single-gesture trials climbed round by round. A full-mix blind
test pitched too hard was told apart every time, round after round, and its misses never pointed at one thing to
fix. A round the person always gets, or never gets, gives no direction: make the next one easier or harder.

**Make the test fair, or it measures the wrong thing.**
- **One gesture per clip, on every side.** Cut at the next onset. When the real clip ran on into the player's next notes, every trial was decided by that and the round was wasted.
- **The same pipeline on all sides.** Two contexts: in the band (the recording's own backing plus the note) and as a stem (the same separation pass run on R, A and B). If R is a separated stem and A is a clean render, the user hears the separation, not the instrument.
- **Fair backing.** The backing under A and B is the recording minus only the real note: its harmonic comb along the pitch curve, its attack, and its top band while it sounds. Subtracting whole stems also removes other parts that share the stem, and that absence gives the fake away.
- **One listening level for the whole page.** Normalise every clip to one loudness. When one clip was nearly inaudible, the user could not answer.
- **Drop trials the sensors cannot follow.** If the pitch tracker cannot follow the real note (pitch error above about 40 cents), lens A copies garbage. Drop the trial automatically and say how many were dropped.
- **Check the answer key before the page goes up.** The key must not alternate, follow a run, or be predictable
  from another factor (the engine, the word, the trial's length). Confirm each key entry against the source
  waveform. A key with a pattern measures the pattern, not the ear.
- **A placement exam needs a reference and a task.** Five clarinet seats heard from row 12, each played alone, were
  all "can't tell" ("I don't get this test"): at that distance the hall's diffuse sound sat about 9 dB over the
  direct sound. Give the ear contrast (the same note moving between two seats, or a fixed reference seat beside
  the one tested) or a closer listener, and say what to listen for.
- **Check alignment.** The onsets of A and B must come from the same detector as R's, run on the full signal. A mask edge once made every A lag 12 ms.

**The listening device is part of the exam.**
- **Record it every round**, on the page next to Submit: laptop speakers, wired headphones, Bluetooth and its codec
  (SBC, AAC, aptX, LDAC), a phone. A listener once switched from laptop speakers to a Bluetooth speaker between
  rounds, and only the page's device field caught it.
- **One device per round.** A round on another device is a new round, not a continuation.
- **Sweep devices when the question is how people will hear it** (phones, earbuds, Bluetooth): the same clips on two
  or three devices. An artifact that grows on a lossy link and not on wired playback is tandem coding (a second lossy
  stage amplifying a first), which is measurable.
- **Calibrate the listener's floor first** with known lenses: the clean render against the same through MP3 at 128
  and 64 kb/s, plus an identical pair. On a Bluetooth speaker one listener heard 64 kb/s at once and 128 kb/s not at
  all, and described the artifact's shape, so keep the words of the tell, not only the score. Then a later "can't
  tell" means something. What a generated track's artifact is remains an open question: neural codec lenses at 6 and
  12 kb/s went unheard in the same rounds, so do not assume them.
- **Log each round's floor** in the song's notes: the ear gets finer, and the next round can aim one notch smaller.

**Page mechanics.** Keep the page in the song (`songs/<slug>/exam/<round>/`), never on a public host when it contains clips of the recording.
- **Always host it, always open it where the person already is.** Serve it on 127.0.0.1 and open `http://127.0.0.1:<port>/<round>/` for them in the browser they can see beside the conversation (a browser pane, preview, web view or browser tool, whichever your harness has). Never send the user a file path or a `file://` page: browsers block audio from file pages, and an external browser means switching away (where a copy button may not work).
- **Submit, don't copy.** A small server serves the round and takes `POST /<round>/answers`, appending them with a timestamp to `exam/<round>/answers.txt`; the user presses Submit and says "done", and you read the file. Keep a copy button as a fallback that also shows the text in a box. Songs are not in the repository, so there is no shared copy: if a song on this machine has one (`songs/tambopata/work/exam_server.py`, `songs/vox/rec/server.py`), copy it into your song's `work/`; otherwise write it (about 40 lines of `http.server`).
- **Check the page in the pane before handing it over**: count the trials and clips, request one clip, run the answer collector, read the console errors (a JS string broken by a literal newline was caught this way).
- Each trial: players, a radio per clip plus "can't tell", and a text box for the tell. Log every round's answers verbatim in the song's `notes/feedback.md`, with the key beside them.

## 3. Reading the answers

- **The ear beats the stem.** Separated stems are biased dark above about 1.5 kHz. A harmonic correction measured against the stems pointed the wrong way (cut the top), while the user heard "upper harmonics too muted". When the ear and a stem-based measurement disagree about timbre, the ear wins.
- **Long-window metrics are blind to fakeness.** 8-bar spectral envelopes within 1.8 dB and stem balance within 0.7 dB, while the user heard "30% there". Report those numbers, but never as proof that a sound is convincing.
- **Every tell is a missing mechanism.** "Too clean" meant pick scrape, air, fret rattle and hum. "Real notes don't start at the hit, but in the middle of the strum" meant the player eases in. "A dip in the middle of the note" meant finger pressure (a level curve). Add the mechanism, not a filter that imitates it.
- **A fit pinned at the edge of its range is a missing mechanism too.** Three times a tone fit hit a bound; each time the fix was physics (a pickup tone knob's LC resonance, a velocity-sensing pickup, frequency-dependent string loss), not a wider bound.
- **Round-trip every sensor first.** Before a sensor reads a recording, run it on synthetic ground truth. The vibrato sensor had three bugs this way (a 10-cent pitch grid, a one-bin share test, a window longer than one vibrato cycle).
- **Register and part-writing masquerade as tone.** Two tone fits failed until the voicings and the bass line moved to the recording's register (measured as pitch percentiles). Check where the parts sit before fitting a sound.

## 4. When single notes pass, the song can still fail

After the blind exam passed, the first full band render was "kinda sounds like him, but the start is weird". Two things that no single-note test can reveal:

- **Note choice, along the real pitch.** A scan of the note numbers is not enough: bends, slides and grace notes land on pitches the score never names (a half-step bend from the minor 3rd lands on the major 3rd and sours a minor chord for as long as it is held). Scan the pitch CURVE of each part (notes plus bend lanes) against what the other parts sound at that moment, at about 10 ms resolution, with two measures: **fit** (spectral pitch-class similarity, Milne et al. 2011: every partial of the note and of the context folded onto a 1200-cent circle; it ranks chord tones above tensions above wrong notes, and a quarter-tone miss near zero) and **roughness** (Plomp-Levelt beating between partials; it catches mistuning and close clusters but NOT tonal wrongness: a major 3rd over a minor chord scores barely rougher than the chord tone). Flag a note when a neighbour a semitone or two away fits much better, weighted by how long it is held; short passing notes will be flagged and are usually fine. Then fix the rules that produced the flags, not the notes one by one: bends land on chord tones (or on a scale tone no semitone from a chord tone, which keeps the blues bend from the minor 3rd to the 4th) and never more than a whole step; each chord uses its own scale (a Dorian G natural over an Ebm9 was the main offender); a rhythm double-stop under a held melody note moves by scale steps, as a pair, to the place whose partials best fit the melody note; rubs that ARE the chord stay (the 3rd against the #9 in a 7#9).
- **Microtiming, at note resolution, with the detector calibrated.** First run the onset detector on YOUR render, where every note time is known. In a dense separated mix a generic onset detector found only 25-54% of the lead's notes and its errors were chance matches (about 20-24 ms of spread from the matching window alone), so the record's "lead looseness" it reported was mostly the rhythm guitar plus detector noise, and a lead humanizer fitted to it came out twice as loose as the player and loosest on the held notes, where a good player is tightest. The listener heard "an amateur trying to play a Hendrix track". Check the round trip itself on something any detector finds (drums: within 9 ms after a steady 16 ms detector delay), then measure the player on notes you can trust: exposed notes whose onsets were refined one by one. There his phrase entries sat either on the grid or 35-75 ms off it (triplet-like places, never smeared evenly), and the notes that runs land on were tight. The model that matched: one monotone time warp over the whole line (notes and every expression lane move together), tight landings on the band's slow push and pull, free entries (on the grid, late or early), and runs re-spaced evenly between them (a lazy start that rushes into the landing, or a push). Per-note random jitter is what sounds amateur; keep it to a few ms. The band's own feel (a shared slow push and pull, late 16ths, a little looseness) made the drums, bass and rhythm guitar sound "less digital and more organic" on the first try.
