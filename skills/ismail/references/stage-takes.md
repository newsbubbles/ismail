# Takes with a person, run by a helper agent

When the person wants a fast loop on the stage (follow someone, perform, record a take, try again), the agent that
owns the scene is often busy with something else: a build, a render, another person's notes. The loop then waits on
it, and a performance loop that waits is a dead loop. So the scene's agent hands the loop to a helper agent (a
sub-agent, a background agent, a second session: whatever your harness offers) with a brief, and goes back to its
work. The helper keeps the loop with the person and hands back what it got. The person asked for this so the way
agent harnesses are built (one agent, many helpers) works for the performance loop too.

Read `stage.md` first (the stage tools, contact with a person in a headset, a Follow is a performance), and
`rigged-characters.md` for the bodies the person plays.

## Who owns what

- **The scene's agent** keeps the scene: models, layout, lights, music, what is saved into the build. It decides
  what the takes are for and what to do with them after.
- **The helper** keeps the loop: it sets people up, listens, cuts voice clips, marks moments, plays takes back,
  keeps or discards them, and talks to the person while they perform. It never changes the scene for real: any
  move it makes is a trial move (`stage_object_set(..., trial=True)`), and it never exports, rebuilds, restarts a
  server or saves an animation.
- **One voice at a time.** While the helper runs the loop, the scene's agent stays quiet in that scene (or speaks
  only through the helper), so the person is not answered twice. The helper says who it is (`sender=`) on every
  line and card.

## The brief (fill this in and hand it over whole)

```
Scene:        <scene name> on the stage server at <address> (stage_status shows it)
Your name:    <how the person will see you, e.g. "takes">  (sender= on stage_say and stage_panel_show;
              who= on stage_listen and stage_events, so presence shows you listening)
Person(s):    <scene person names, e.g. person_bar_lean>, played by <body> (world.json actors)
Goal:         <what the take is for, e.g. "Sam at the bar, 20 s: he hears his name, turns, raises his glass">
Setup:        pins:  <e.g. hips on stool_3>      (stage_follow_anchor, or a saved map: stage_control_map(preset=))
              drives: <e.g. legs by the hands, touch to start>   (stage_control_set)
              where the person stands, what they see first (a card or a pin waiting for them)
Takes:        how many tries are fine, how long each, what makes one good (in the person's words if they said it)
Voice:        record (every Follow does), what to cut into clips, what the lines are if any
You may use:  stage_listen, stage_busy, stage_events, stage_presence, stage_say, stage_panel_show, stage_panel_close,
              stage_actor_follow, stage_actor_play, stage_actor_stop, stage_follow_anchor, stage_control_set,
              stage_control_map, stage_actor_profile, stage_perform, stage_performance, stage_take_start,
              stage_take_stop, stage_take_keep_last, stage_take_view, stage_take_note, stage_actor_map_save,
              stage_batch,
              stage_object_set with trial=True only
You may not:  change the scene for real, save animation, export or rebuild, restart anything, speak in other
              scenes, keep going after the person says stop
Stop when:    <e.g. one take they keep, or 30 minutes, or they say done>
Hand back:    see below
```

## The loop (what the helper does)

1. **Be present first.** Start listening (`stage_listen(who=<your name>)`) before saying anything, and greet the
   person once with what is about to happen. A note nobody hears is the worst failure on the stage.
2. **Set up in one call.** Pins, drives and the first card together (`stage_batch`), then tell them how to start
   (the Follow button, or the touch balls).
3. **While they perform, be quick and quiet.** Watch `perform_*` events; cut a clip when a part is worth reading
   (`stage_perform(action="next_clip")`), mark moments (`action="mark"`), change a drive if they ask. Do not speak
   unless they ask you something (then `stage_say(..., aloud=True)`).
4. **After each take,** it plays back on the person with their voice. Ask keep or redo in one short line or a card;
   keep with `stage_take_keep_last(name=)`. Read what they said with `stage_performance`.
5. **Change one thing per retry,** and say what changed.
6. **Save a map that worked** on the actor (`stage_actor_map_save`), so the next session starts from it.

## Hand back (to the scene's agent)

- The takes kept: their ids, who, how long, and the person's verdict on each in their words, verbatim, so the
  scene's agent can keep them in the lexicon (`user-experience.md`, "The lexicon: their words, our terms").
- The performances: ids, and the clips' words that matter (lines, notes they spoke about the take).
- What was set up: pins, drives, maps saved and their names.
- Anything the person asked for that is not the loop's: scene changes, stage problems (a gesture that misfired, a
  panel in the wrong place), with the event ids. Stage problems go on to the stage dev.
- What is left: takes still wanted, and what blocked them.
