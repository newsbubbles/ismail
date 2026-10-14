# Dev copies (git worktrees): the decision tree

Read this when you change ismail itself as a dev: the contributor dev, an area dev (the stage dev so far) or a
helper agent one of them started. Every dev works in its own copy of the repository (a git worktree), so a live
set, the phone page and the stage keep running from the main checkout while code changes elsewhere. Branches
alone would put every agent in one folder and switch code under running servers; copies keep them apart.

Copies go stale, get forgotten and lose their owner. `python -m ismail.worktrees` keeps track, so nobody has to
remember: its first line says what to do next, and the intake (`python -m ismail.handoffs`) shows the same summary
and every copy that needs something.

## Who touches git at all

| who | where they work | git |
|---|---|---|
| the person | anywhere | never needs to know it exists |
| studio agent (song, set, film, scene) | its song folder | none, ever |
| contributor dev | one copy per topic; catches the main checkout up | branches, merges, pull requests |
| area dev (the stage dev) | one copy per topic, in its own part | branches, then a local merge request to the contributor dev |
| helper agent | the copy its parent names | commits there only; reports the branch back |

## The tool

```bash
python -m ismail.worktrees                         # the next action, the main checkout, one line per copy
python -m ismail.worktrees list --fetch            # the same after a git fetch (it says how old the data is)
python -m ismail.worktrees list --unassigned       # only the copies with no owner
python -m ismail.worktrees new <topic> --owner <name>
python -m ismail.worktrees claim <path> --owner <name>
python -m ismail.worktrees sync <path>
python -m ismail.worktrees done <path>
python -m ismail.worktrees sweep [--dry-run]        # remove the empty folders removed copies left behind
```

A copy's line gives its path, branch, owner, commits ahead and behind `origin/main`, whether it is merged, its last
commit's age, its uncommitted and untracked files, and its open pull request. Its flags: **UNASSIGNED** (no owner),
**MERGED** (its work is in main: remove it), **IDLE** (unmerged, no commit for 3 days), **BEHIND** (main has
commits it lacks). Owners are recorded in the repository's shared git folder, so every copy sees them and nothing
enters the repository.

## The tree

1. **Am I a dev?** No: never run git and never write outside your song folder. Yes: go on.
2. **Starting a topic.** `python -m ismail.worktrees new <topic> --owner <your role>`. It fetches and makes the copy
   from the project's main as fetched (`origin/main`), never from the local main, which may be behind. One topic per
   copy, one agent per copy. If it refuses because someone already has the topic, ask the person who builds it,
   then stop.
3. **A copy with no owner.** Copies made before the tool, or by a harness, have none. If it is yours:
   `claim <path> --owner <your role>`. If it is not, leave it and tell the person it is there; never claim someone
   else's copy, and `--reassign` only with the owner's or the person's yes.
4. **Which skill text am I reading?** Read the skill where your harness loads it from, not your copy's, unless your
   topic is the skill itself. Then your copy holds the new text, and the loaded one is what everyone else still
   reads.
5. **My copy is behind main.** `sync <path>` merges `origin/main` in. Commit your changes first: it refuses
   uncommitted work, and nothing is ever stashed (the stash stack is shared by every copy). On a conflict the merge
   stays in progress and the tool lists the files: resolve each one and explain each conflict to the person in
   their own terms, at their level. `CHANGELOG.md` conflicts usually keep both sides.
6. **The main checkout is behind.** The first lines of `list` say so. The contributor dev catches it up, but only:
   - when no live engine or server runs code from it (a set, the phone page, the stage): an update under a running
     engine can load new code in the middle of a set;
   - when nobody's uncommitted work blocks it. If someone's does, it goes on a branch with the person's yes and into
     a small pull request, never reverted or stashed.
7. **Testing my branch.** Run the tests from your copy. Shared servers (the phone page, the stage, exam pages) keep
   running the main checkout's code. Never restart a shared server onto a branch: to see a change live, merge it
   first, then restart when nothing is playing.
8. **Handing back.**
   - Helper to parent: the branch name and what the tests said.
   - Area dev to contributor dev: a local merge request (the branch, what changes, the tests passing).
   - Contributor dev: merges locally, or opens the pull request after the person's yes.
9. **Merged.** Whoever merged runs `done <path>` at once: it removes the copy and its local branch and drops the
   owner record. The project deletes the remote branch by itself.
10. **Not cleaned up.** `list` flags it:
    - **MERGED:** `done <path>`. It refuses a copy with uncommitted or untracked files (caches aside): look at
      them, and move what matters to `_reclaim/` in the main checkout. Never delete them.
    - **IDLE:** an unmerged copy with no commit for 3 days goes back to its owner, or to the person when it has none.
      `done` refuses anything unmerged: open or finish its pull request, or ask the person before dropping it.
      Nothing unmerged is deleted without a yes.
    - **An empty folder left behind.** On Windows git cannot remove a copy's folder while a program's working
      folder is inside it: the files and git's record go, the empty folder stays, and `list` never shows it again.
      `done` says HELD when that happens; the intake counts such folders. `sweep` removes every empty folder next to
      the main checkout named `<main>-*`, and in `<main>/.claude/worktrees/`, that git does not list as a copy, and
      drops the owner records of copies that are gone. It removes only empty folders (never anything inside one);
      one still HELD stays for the next sweep, and a non-empty `<main>-wt-*` folder git does not list is named for a
      look, never touched. `--dry-run` lists what it would remove.
    - **Never leave a shell's working folder inside a copy:** use `git -C <copy> ...` or a subshell
      (`(cd <copy> && ...)`), so nothing holds the folder when the copy is removed.
11. **Two agents want the same file.** The owner of that part decides (`development.md`, the roles). Never two
    copies on one topic.
12. **A different harness** (another agent app, a command line, an editor). Nothing here depends on one harness's
    worktree feature: plain `git worktree` works everywhere. Where a harness makes its own copies, treat its folder
    as yours for that topic and `claim` it.

The tool never passes `--force` to git, never stashes and never deletes a file: `done` removes only a copy whose
work is all in `origin/main` and that holds nothing git does not track, and `sweep` removes only empty folders.
