# The named-ops guard (optional, Claude Code)

`named_ops_guard.py` is a PreToolUse hook. Before a shell command or a file edit runs, it checks for the off-path
forms below and refuses them, naming the ismail op that does the same thing. An off-path command needs a fresh
permission each time, and a permission prompt during a VR session throws the person out of the headset; the named
op (`python -m ismail <op> ...`) runs under one allow rule, `Bash(python -m ismail:*)`, and does the checks, history
and page updates the hand-made command skips.

The same rule holds in any harness: `references/stage.md` has the table. This file is only the Claude Code add-on.

## What it refuses, and what to use

| Refused | Why | Use |
|---|---|---|
| `python -c` (also `python3`, `py`, a heredoc or a here-string piped to python) with ismail code | skips the op's checks, undo and events, and prompts every time | `python -m ismail <op> key=value` |
| `cp`, `mv`, `rsync`, `copy`, `move`, `xcopy`, `robocopy`, `Copy-Item`, `Move-Item`, or `shutil` in `python -c`, into a stage scenes folder (any source, including `AppData/Local/Temp/claude`) | overwrites with no history and the page never hears of it | `stage_asset_install(scene, file, kind, name=)` |
| `Stop-Process`, `taskkill`, `kill`, `pkill` aimed at python, ismail, port 8862, or a pid or port in the stage or live registry | kills the room (or the music) under the person, often in VR | `stage_restart`, `stage_stop`, `live_stop(project=)` |
| Write, Edit or MultiEdit of `world.json`, `scene.glb` or `manifest.json` in a scenes folder (also `>` redirects, `Set-Content`, `Out-File`, `Add-Content`, `tee`, `sed -i`) | no check, no history, the open page keeps the old world | `stage_world(scene, update={...})`, `stage_asset_install` |

A scenes folder is a `scenes/<name>` folder that is under `vr/scenes/`, under a running stage server's scenes, or
holds a `scene.glb`. Both slash styles and both drive forms (`D:/...`, `D:\...`, `/d/...`) are the same path.

Everything else passes with no output. `python -m ismail ...` always passes. If the hook cannot read its input it
lets the command through: a guard bug never blocks work. It uses the Python standard library only and answers in
well under 100 ms.

## Turning it on

Install this repository first, so the file is on disk, then add this to `.claude/settings.json` (the project's)
or `~/.claude/settings.json` (every project), with `<path>` the folder that holds this repository:

```json
{"hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell|Write|Edit|MultiEdit",
  "hooks": [{"type": "command", "command": "python -S -c \"import os, runpy; p = '<path>/skills/ismail/hooks/named_ops_guard.py'; os.path.isfile(p) and runpy.run_path(p, run_name='__main__')\""}]}]}}
```

Paste this form, not `python <path>/named_ops_guard.py`. A hook that exits with code 2 blocks the call, and Python
exits 2 when the file it is given does not exist. A mistyped path, a checkout that has not caught up yet, or a folder
that was moved would then block every shell command and edit in every session, including the one that would fix
it. The `os.path.isfile` check passes every call through until the file is there. Inside the guard, any error of
its own also lets the call through. Only a refusal exits 2.

The guard's own work takes a few milliseconds; most of a call is Python starting. Where that start is slow (many
packages installed), `python -S` skips site-packages and starts faster; the command above uses it, since the guard
needs only the standard library.

A refusal reaches the agent as the hook's message (exit code 2), so it switches to the op without asking the person.
