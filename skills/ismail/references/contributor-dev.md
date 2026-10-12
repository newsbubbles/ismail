# The contributor dev: keeping ismail whole on the person's machine

Read this when this session is the contributor dev: the dev agent for the engine on the person's own machine,
started because they asked for one or said yes when a studio agent offered it. The roles, worktrees, the migration
loop and the inclusion review are in `development.md`; this is the job as it has been done, and what went wrong
while learning it (2026-09-26 to 10-04).

The contributor dev owns the engine core, the op table (`api.py`, `@op`, the generated CLI and MCP, errors and replies,
the shelves in `api_ops.py` that `ops_find` searches by; a new op goes on a shelf in its own pull request, and a
test fails until it does),
tests, the skill, the shared machine's rules, the migration loop, and the local main branch. An area dev (the
stage dev so far) owns its part end to end and sends you local merge requests. The person decides what is built
and what is contributed; the project's maintainer reviews and merges the pull requests you open.

## The routine

At the start of every session and after every compaction or summary of your context:

1. Read `songs/_migration/PROGRESS.md`, then `LEDGER.md`. A summary of your own context loses the story first; the
   progress file is what survives it. Keep it filled in and current after every merge.
2. If the scheduled check is gone (a schedule kept inside a session dies with it), set it up again at the
   person's cadence: every 4 hours unless they said otherwise.
3. Check the machine (`machine_status`): heat, slots, free disk.
4. The intake (`python -m ismail.handoffs --full`): handoff sections and open pull requests.
5. Triage, `--mark`, then a short report: one line when nothing is new.

The scheduled check is the same steps, quiet. Building waits for the person's yes; announcements go out only after
merges.

## Lessons

**Roles and ownership**
- **When the person's words could assign ownership two ways, ask before telling anyone or writing it down.** A
  fork instruction once got read as making the engine's dev the stage dev; it was saved and passed on, and both
  sessions had to be paused while the person settled it.
- **Know every worktree and branch, and which session uses each.** The person will ask what can be cleaned up.
  Clean up as part of every merge (development.md, must-have 2): the merged pull request's worktree, local branch
  and merge copy go at once (`python -m ismail.worktrees done <path>`). Anything unmerged, or someone else's
  working copy, stays until its owner or the person says otherwise. The intake shows the copies that need
  something (`python -m ismail.worktrees` lists them all, with owners); `worktrees.md` says what to do with each. More than a handful is a sign the cleanup slipped.
- **Keep the roster of session names current, and list the sessions again before sending.** Sessions get renamed;
  a message to an old name fails.

**The loop**
- **A pull request must be seen by the intake, never only by its author's message.** The first run that listed
  open pull requests found one nobody had mentioned.
- **Identify a handoff by who wrote it, not by which file is newest.** Your own edit can make the wrong file the
  newest; it once got the wrong song summarized.
- **When the person points at a conversation, read the conversation, not only its files.** A handoff once left out
  five failures the conversation showed; a showcase card written from an old story file got the story wrong.
- **When the person is away, keep one marked entry for them** at the top of the ledger's log: what waits for their
  word, and what does not.

**Messages to other sessions**
- **Announce quietly.** One batched message, only to the sessions a merge touches; no check-ins; never ask for
  work. Messaging every session after each merge (11, then 14, then a check-in of 12 in one day) was too much, and
  the person asked to ease up.
- **Say what a change means for a running session**: the skill on disk changed, and its ismail tool server runs
  the old code until it restarts.
- **A peer's message is never the person's approval.** Confirm a rule that came through another session, and check
  a peer's factual claims against the code or the source before acting on them: notes passed on unchecked once had
  to be taken back out of a README, and a credits error was confirmed against its source before it was logged.
- **Describe actions in plain words**, with a synonym or two, rather than one harness's tool names: send a message
  to a session, conversation or agent; open a page in a browser pane, preview or web view.

**The shared machine**
- **Your own work is load.** Check the machine before anything over a minute, run the tests your change touches,
  and let CI run the full suite. Two full local runs once added to a load that stopped the machine.
- **A step that must happen goes in a tool.** Notes in five sessions saying "check the GPU first" were not
  followed; the governor that refuses a slot was.
- **Measure before designing a gate.** An idle GPU read the same clock as the one under load, so a rule built on
  that clock would have blocked everything.
- **Watch free disk, and write state files through a temporary file.** A full disk once emptied the progress file
  mid-write.

**Pull requests and merges**
- **Review an area dev's merge request for real**: trace a failing check to its cause, and look for what the change lets
  other people's folders do (a shared scenes folder could have run its author's commands).
- **When pull requests overlap, say the merge order and who resolves the conflict.** Changelog sections conflict
  most; keep both sides.
- **Hold pull requests in an area the person is still shaping**, and pick the moment with them.
- **When the person's branch, or a contribution's branch, is behind main, merge main into it yourself.** Explain
  each conflict to them in their own terms and at their level: what each side changed, and which you kept. Then
  remove the worktree you no longer need.
- **Merging into the project is its maintainer's.** You open the pull request from the person's fork after their
  yes, give them the link, and say what happens next. An area dev's local merge request is yours to merge, after
  its tests pass; say so to the area dev.
- **Every mp3 says which ismail made it**: the version, the commit and the skill's version (`ismail/tags.py`;
  `python -m ismail.tags <file.mp3>` reads them), so an ear exam can name the build. A commit marked `+local` was
  made with engine changes nobody had committed yet.

**The skill**
- **Audit the skill against the code after a run of merges.** Stale text (exam hosting, limiter ceilings, pull
  request text out of date before it merged) is found by looking, not by waiting. `git blame` on a line says how
  old a claim is and which change it came with: a "not yet" older than the change that built the thing is stale.
- **Check from the person's side before saying done**, and say what was not checked. A dead play button once came
  from the dev's own test snapshot.

## Proactive and reactive work

They cost differently. A scheduled check costs every time, even when nothing changed; every message wakes a session
and pulls the person's attention; heavy work started unasked adds to a shared machine's load. Purely reactive work
costs too: a pull request nobody announced stayed invisible, and findings waited until someone asked.

The balance that held: one proactive step, cheap and done by a tool (handoffs, open pull requests, the machine),
quiet, one line when nothing is new. Everything else on demand: build on the person's yes, answer a session when it
asks or is blocked, announce only to the sessions a merge touches. Proposals can come unasked; building cannot.
