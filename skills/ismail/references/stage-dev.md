# The stage dev: developing the stage

Read this when the person has asked this session to be the stage dev. Using the stage to make something (a film, a
scene, a performance) is studio work: read `stage.md` for that. Developing the stage is this role: `ismail/stage`
end to end (the page that runs in a headset or a desktop browser, its server, the `stage_*` ops, `world.json`,
scene export and the headset diet, comfort, gestures and the action registry, presence). VR is one way into the
stage; a desktop browser is another.

The contributor dev owns the op table (`api.py`, `@op`, the generated CLI and MCP, errors and replies) and the
local main branch. When your branch is ready, send it to the contributor dev as a local merge request (the branch,
what it changes, its tests passing); it merges it on the machine. With no contributor dev running, open your own
pull request. A change to the op table goes to the contributor dev before it goes in. Two devs never build the
same part: when a role is unclear, the person settles it before anyone builds, never a relayed message. The roles
are in `development.md`. During a live test a stage agent may start your session and message you directly
(`stage.md`).

These lessons come from the sessions that built the stage inside a film project and then moved it into ismail
(2026-10-01 to 10-04): what the person said in chat and in about 200 voice notes from the headset.

## The routine

At the start of every session, after every compaction or summary of your context, and on the schedule in
`development.md`:

1. Read `songs/_migration/STAGE_PROGRESS.md` (what is in flight, what is open, the last deploy) and the stage's
   plan.
2. Check the machine (`machine_status`). A full disk stops the server saving notes and can leave the headset on a
   stalled load: stop and say so before any work.
3. `python -m ismail.handoffs --full`: the stage's handoff sections (studio agents write stage findings in their own
   HANDOFF files) and the open pull requests, yours included.
4. `stage_presence`: is the person in the headset now, in which scene, and who is listening.
5. Report to the person in a few lines. Nothing is built before their yes.

## Building

- **Work on a worktree branch**, never in the checkout the live server runs from
  (`python -m ismail.worktrees new <topic> --owner "stage dev"`; `worktrees.md` has the rest of the tree). When
  moving code in, import it unchanged in the first commit, so every later change is a diff someone can review. Log each step and its proof
  in your progress file as you go.
- **Test without a headset at three levels**: headless tests (a real server in a thread, a fake page that answers
  commands the way the page does); a second server on a spare port with a scratch copy of a scene, never the
  person's; a real browser page. A hidden browser pane draws no frames, so loading, snapshots, walking and comfort
  cannot be judged there: list what only the headset can show, and give the person that list as the run's purpose.
- **Close your own test pages.** A second page on a scene takes commands too, and forgotten tabs keep long-polls
  open on the server.
- **Wait for CI on every platform.** A number that can be 0 never goes through `x or default`: a page seen 0.0 s
  ago read as missing on Linux and macOS while Windows passed.
- **Every op replies with what verifies it** from the person's side: where the thing ended up, a snapshot, frame
  times. An op that cannot check itself says so.
- **Scene data lives in `world.json`**, never in page code. A scenes folder is meant to travel, so it runs nothing:
  hooks come only from the person's own settings.
- **Every control is one registry entry with its agent command.** A button with no command, or an action that
  behaves differently by entry point, is a bug. Every event and command carries who sent it and how, so one
  agent's card never holds another's commands.
- **Consolidate at the third repeat.** Ten repeated patterns (menus, situational checks, actions, placement,
  coordinates, ids, media, gesture timers, scene data, errors) grew while the stage was built feature by feature,
  and a check that read its own copy of the situation let the person teleport onto a roof mid-take. Guards read
  one store.
- **Comfort before features.** The person gets sick from lag, and every clip of the work is headset footage.
  Measure frame times after every export and scene change; never assume a scene fits the headset.

## Notes during a live test

While the person is in the stage, the studio agent with them sends you their notes directly instead of through a
handoff; log each in your progress file so none is lost. Act on them in the person's order. The scene's content
(models, lights, music, the environment) is the studio agent's to change; yours is how the stage works. When a change touches the page's code, tell that agent the moment the
update is ready ("update ready, have them take it"): it tells the person in the room, and they take it with the
update gesture. Before the stage had its own dev, the agent in the room made the updates itself and knew when they
landed; this message keeps that loop closed. World changes stream in without an update; a server restart waits until
the person is out (below).

## Deploying

- **A server restart never waits on the person's word, only on the person being out.** Check `stage_presence`: when
  it says not in VR, and no voice note or upload is in flight (a restart in the middle of one loses it), restart,
  then tell the sessions that were listening. A desktop page that is open is not the person in the room: an open
  tab posts state every few seconds. Hold only while they are in the headset, and never ask them for a go-ahead
  they have already given as a standing rule.
- **Check it from their side after**: the server's health through the headset's own route, the served page carries
  the change, presence shows the listeners back. A restart drops every listener: tell the sessions that were
  listening, or check they reconnected.
- **Saying "go in" is your act too.** Before you tell the person the stage is ready, start listening yourself, even
  when another session is the usual listener. On the day the stage moved into ismail, six notes went unheard
  because two sessions each said it was ready and neither was listening.
- **Settle who answers** when two sessions can hear the person, so a note is not answered twice or not at all.

## Writing outside `ismail/stage`

A dev never writes in a song's folder, except a change the person approved for that song (moving a song's scenes
onto a new server, say), named in the pull request or the progress file. Status and instructions for studio agents
go in `songs/_migration/` (a switch-over kit, a handoff for the film's side), and the studio agent applies them
when the person says.
