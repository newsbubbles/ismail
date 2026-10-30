# The person: working with the human in the loop

The agent cannot hear or see; the person can. Everything in ismail that turns a draft into something good went
through their senses and their words: blind exams, eye exams, a feedback log, a lock written into code. This
reference is how an agent works with the person so the work improves and their culture is kept: their **words**
(the lexicon), their **senses** (exams and pages), their **intent** (objectives) and their **consent**.

Read it at the start of a session that works with a person, beside SKILL.md.

## What we record, and what we never record

- Record: the person's words about the work, their answers to exams, the decisions they lock, and the objective
  they set for a piece.
- Never record emotion, mood, health, personality or any inference about the person. The lexicon measures their
  eloquence in the vernacular, and only that. "The snare is boxy" goes in; "the user seemed frustrated" never does.
- All of it stays on the machine (`songs/_user/`, `notes/feedback.md`). A quote or a number measured from the person
  goes public only after they say yes to exactly what would be published (development.md, the inclusion review).

A newcomer is a person, not a computer. On a machine where someone already makes songs, `guide` opens normally;
when the person in front of you is new (they say so, or a friend sits at the owner's computer), call
`guide(new_person=True)` and run their first session. Keeping their sketch marks nothing for the owner.

## The rest waits for its moment

The first session stays narrow: two sentences, two questions, sound in minutes. The two short sentences say what
this is (every note and sound written by the agent, a song they change one piece at a time, any part of music at
their level), never as "this isn't X, it's Y", and every sound comes with the file in hand and a question: "a first try, I don't know your taste
yet; what would you change, or name a song you love". Everything else ismail can do (the
phone page, live play, the VR stage, building an instrument from recordings) is offered later, each at the moment it
answers something the person just did or said, in one sentence they can say no to:
- they keep a sketch or say they like one: live play ("Want me to play it live and change it while it plays?
  Call out more bass, a break, anything."), and the phone ("You can hear it on your phone and talk back to me while
  it plays.").
- they want to jam, perform, or hear the music change while it plays: live play.
- they mention a VR headset, or want to see the music: the stage.
- they step away from the computer while something plays: the phone (phone.md, "When to offer it").
One feature per moment. A no holds for the session. Never list them all at once: a list of features up front is
what the intro avoids (the person, 10-07: the intro "needs to be a specific scope", and "an agent can let people know that
those features exist").

## Their first answer decides the words

In a first session the first question carries "and do you play?" (guide's FIRST SESSION block). Their answer sets
your vocabulary for the whole session, and `guide(first_answer=<their words, verbatim>)` says which:
- **A musician**: they name an instrument they play, a style they trained in, or that they read music. Use their
  words from then on: keys, voicings, "the left hand", stops and registrations for an organist, the pocket for a
  drummer, bars and chord names. Ask what they would call a thing before you name it, and don't explain what they
  already know. (Dress rehearsal 1, 2026-10-05: a trained organist who reads music, and nothing told the agent to
  use her background.)
- **Plain words** for everyone else: say what a thing does ("the low notes", "the part that comes back"), times in
  minutes and seconds, no keys, chord numbers or Hz until they use them first. They also get hand holding: version 1
  plays at once with one line of delight, never "which is closest" or "what's missing", never three to compare (the
  rest are spares); every round offers two or three playful choices in everyday words, made from this sketch
  ("faster and bouncier", "add a beat you can nod to"), never one they already asked for ("bouncy" in the brief
  gets no "bouncier"), plus "or tell me anything"; each change plays at once, new
  version first, with one plain line on what changed. `sketch` writes those choices into its reply.

If they later say they play or read music, call `guide(first_answer=<those words>)` again and switch. Their words go
into the lexicon as they come (below).

## Producers: say where the stems are

A person who produces in a DAW (they name Ableton, FL Studio, Logic, Reaper, a plugin, "my studio", or say they
make beats or mix) will want the parts as audio, but they will not assume ismail has them and will often not ask.
Tell them at the right moment, once, without being asked:
- the first time a version of their song is worth keeping (they like it, or ask for changes to it rather than a
  new idea), render with `render(stems=True)` and give the full path of the folder: `<project>/renders/stems/`, one
  WAV per track, plus `bus_<name>.wav` per bus. A track sent to a bus is in its own stem and in the bus stem, so
  import either the tracks or the buses, not both (they would double).
- with it, offer the notes as MIDI (`midi_write`) for the parts they may want to replay on their own synths.
- after that, say the stems folder was refreshed when you render stems again, in one short line; don't repeat the
  explanation.

## The lexicon: their words, our terms

A music culture lives in how its people talk about sound: "the pocket", "boxy", "too clean", "30% there", "muddy". Those words carry what the ear and eye noticed. An agent that keeps them, maps them to what ismail does, and
says things back the person's way loses less in every request. Kept over months, the lexicon is a record of that
culture and of how the person's craft grows.

**When to note a word.** Every time the person names a quality, a problem, a fix or a place in the music, and the
first time they use a trade term. Note it at once, verbatim, before acting on it:

```
lexicon_note(project, said="the snare is boxy", craft="mixing engineer", where="exam 7, bar 33")
```

The reply shows what the same words meant before, if anything. Use that first.

**Map it once you act.** When you know what the word meant in ismail terms (an op, a parameter and its direction,
an effect, a measurement), add it, and add the outcome when the next answer or exam shows whether it worked:

```
lexicon_note(id="L0007", means=["fx eq peak 400 Hz -3 dB on snare", "analyze_timbre centroid"])
lexicon_note(id="L0007", outcome="worked", why="exam 8: snare passed")
```

The words never change; the mapping and the outcome do, and the file keeps every version.

**Read it.**
- At the start of a session: `lexicon_view` (by craft, outcomes, the newest entries, words not mapped yet).
- Before acting on a word: `lexicon_find(text="boxy")`. A word that "worked" last time is the first thing to try.
- Before explaining a change: `lexicon_find(text="eq peak")` finds the person's word for it, so say "less boxy"
  instead of "a 3 dB cut at 400 Hz", unless they speak in Hz themselves.

**Names for the parts.** Your track names are yours: a listener who said "the bass overpowers it" meant the answering
bass, the agent turned down another one, and three rounds went by before anyone noticed. In a song's first feedback
round, and whenever a note's words could mean several parts (`feedback_read` says so: "says 'bass', and 3 parts could
be it"), add the parts block: `feedback_page(..., parts=True)` plays each part alone and in the mix where it stands
out most, with a box for their name. Then keep each name with the part it names:

```
lexicon_note(project, said="the answer bass", track="reese", heard="r3")
```

`heard=` checks it against what they typed in that round, so a name is confirmed only by them; a name you inferred
(`track=` without `heard=`) stays marked as not confirmed. From then on, use their name for the part.

**Pictures have a vernacular too.** Words about how something looks ("waxy", "cluttered", "flat", "the shadows are
muddy") go in the same lexicon, with the visual crafts (director, cinematographer, colourist), mapped to what
changed: a material setting, a light, a grade value.

**Crafts.** Each entry names the role the word belongs to, the roles a record used to need people for: composer,
arranger, performer, sound designer, recording engineer, mixing engineer, mastering engineer, producer, DJ,
director, cinematographer, colourist, editor, choreographer, listener.

**The learning curve.** `lexicon_view` shows the share of trade words in what the person says, by month, and which
crafts their vocabulary grows in. Use it to pitch explanations at their level and to notice when they want finer
control (they start naming frequencies, so show them frequencies). It is never a grade.

**Other people.** A friend's or a client's feedback goes in with `who="dj friend"`, and only with that person's
consent, the same as the user's.

## Their senses: exams and pages

The method is in `blind-tests.md`. Editing through pages built for the person's senses (audio pairs, stills, clips,
a view of the stage) has been the fastest route to a good result. The rules that hold for every sense:

- Show the thing before asking about it.
- One named change per lens against a fixed base, the current state included, and moves big enough to hear or see.
- Two or three numbered questions, one per variable.
- Log every answer verbatim in the song's `notes/feedback.md`, carry a lock into the build as a constant, and note
  any new words in the lexicon.
- Bring the page to where they are. Away from the desk the phone page is their whole channel (`phone.md`): exams
  with `phone_exam`, questions with `phone_ask`, answers with `phone_say`. One person (10-06) loved doing their voice tests
  on the phone and spends whole days away from the desk: plan exams that work on a phone and
  earbuds.
- A desk page that takes their voice or mic opens in their own browser, not an app pane (a pane has no
  microphone), and brings back everything they typed or said when reopened: `feedback_page` does all of it,
  `feedback_wait` returns when they press Send, and `feedback_read` reads their answers. A listener elsewhere (a friend
  the song is for) gets the shared copy, `feedback_page(share=True)`: host its folder where they can open it; viewing
  is all they need. Their typing and voice notes stay in their browser until "Send my answers" sends them as a
  message (the share menu, or a copy to paste, with the voice notes as files), and `feedback_paste` reads that
  message into the round as their Send. Every page also has "Copy my answers". For a round on a whole song's
  structure, offer `feedback_page(..., moments=True)`: "mark anything that strikes you while it plays". A button (or
  the M key) puts a pin on a time strip under the song, with tick boxes (too much, too empty, which part) and their
  words, and `feedback_read` lists each moment with its time and its bars. A hand-built page only for what it cannot do (`blind-tests.md`, page
  mechanics).

## Bars or time: their unit

ismail's habit is bars, as in a DAW. A casual listener thinks in time: "the part at two minutes", "the last 30
seconds". Both are measured, so answer in their unit. Lines from the phone carry the bar and `into_s` (seconds into
the piece); the page has a Bars/Time switch. Say bars only to someone who works in bars (a producer, a musician
reading along), and minutes and seconds to everyone else, the way a music player shows them.

## When the person is inside the work

In a scene, a headset or a live set, the person cannot see your terminal. `stage.md` has the practice: answer at
once and briefly, capture what they point at when they speak, show which version runs, address every note, and
choose the surface (image sheet, stage, render, page, panel) where each decision is fastest for them to make.

## Their intent: objectives

Every piece states what it is for, in the person's words: `project_set(objective="keep a listener asleep for 3
hours, nothing sudden after midnight")`, or `project_new(..., objective=...)`. A changed objective is a new entry;
the earlier ones stay as history. `project_info` shows the current objective.

- Judge drafts against it. A sleep set that scores well but jumps at 2 a.m. fails its objective.
- **Intent provenance.** A version, remix or derivative is made with `project_new(..., derived_from=<the original>)`;
  it carries the original's objectives and lineage, so anyone reading it sees the intent it came from and how its own
  differs.
- An objective set by an agent says so (`objective_by="agent"`) and is confirmed with the person before it guides
  the work.
