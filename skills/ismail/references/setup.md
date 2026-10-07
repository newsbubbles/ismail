# Setting ismail up for a person

The goal is that the person hears a sound made with ismail on their own machine, having done as little as
possible themselves. They may be on a phone call with someone helping them, so every instruction you give them
is short enough to read aloud. Setup errors are yours to solve, not theirs: an onboarding that hands a person a
stack trace has failed.

## 0. Can you run commands on their computer?

ismail runs on the person's machine. You need a terminal: a coding agent (the Code tab in Claude's desktop app,
Claude Code, Cursor, Codex, or any agent that runs commands and can call MCP tools). A chat assistant in a browser
cannot set it up. If that is you, say so plainly, and tell the person which app to install and what to type in it
first. Installing that app is the one step only they can do. In one sentence, without explaining anything else: "I
can't run programs on your computer from this chat. Claude Code, the Code tab in the Claude desktop app, Cursor or
Codex can; paste the same line there." Name only what works (the list above), and never go on to explain skills,
plugins or servers.

**Their words, not ours.** Never say skill, plugin, MCP, uv or server to the person, and never ask them to find,
install or understand "the skill", unless they used the word first. Say "the add-on that gives me ismail's tools"
and do it yourself. When a step needs them (the two plugin lines, an Allow box, the restart), say what they will see,
in plain words, and nothing about what it is called.

**Say what's coming, before your first command.** In an app that asks before each new tool (Claude's desktop app,
Claude Code), tell them once: "You'll see about 10 to 20 Allow boxes in this window. Each one is me or ismail asking
to use a tool on your computer. Click Allow (or Always allow, so that one stops asking)."
<!-- How the 10 to 20 was counted (2026-10-06): the app asks once per tool the first time it runs: once per ismail
tool (per tool, not per server) and once per shell command its permission mode doesn't already allow. On a machine
missing everything, setup runs about 3 to 5 checks (section 1), up to 4 winget lines and a PATH reload (2), the
warm-up, up to 2 plugin lines and 1 or 2 for the songs folder (3); the first session then calls 3 to 5 distinct
ismail tools (guide, sketch, sketch_keep, lexicon_note, machine_status) and 1 to 3 play commands. That is about 7 to
23, fewer when they press Always allow. To recount: walk sections 1 to 4 and guide's FIRST SESSION block and add
them up again; keep README's "Use it with Claude Code" in step. -->

## 1. Look before you install

Find out what is there before you change anything:
- the operating system and its version;
- Python 3.10 or newer (`python --version`, or `python3`; on Windows also `py -0`);
- `uv`, `ffmpeg` (for mp3s) and `git`;
- free disk space (about 2 GB is plenty; the optional sample sets add 20 to 520 MB each).

Write what you found in one line to the person ("You have Python 3.12; I'll add ffmpeg and ismail"). Don't list
versions at them.

## 2. Install what is missing, yourself

Use the system's package manager:
- **Windows:** one line per missing tool, each runnable as written:
  ```
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  winget install -e --id astral-sh.uv --accept-package-agreements --accept-source-agreements
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
  ```
  A new PATH does not reach a shell that is already open, and inside a desktop app "open a new shell" is not
  enough either. In PowerShell, reload it in place:
  `$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')`,
  or call the tool by its full path.
- **macOS:** Homebrew (`brew install python@3.12 uv ffmpeg git`). If Homebrew is missing, installing it asks for
  their password: tell them before it asks.
- **Linux:** the distribution's packages (`apt install python3 python3-venv ffmpeg git`), then uv from astral.sh.

Before any prompt that only the person can answer (an admin password, a Windows "Do you want to allow this app"
box, your own harness asking to run a command), say in one line what will appear and what to press. One step at a
time. Don't ask "shall I continue?" between routine steps. Ask only for decisions: what to make, a download over
about 100 MB, anything that costs money.

## 3. Install ismail

**Name every wait before it starts.** Say what is happening and about how long, and say it again if it runs past
that ("still building the audio libraries, a minute or two more"). Minutes of silence read as broken. The waits a
new person meets (dress rehearsals 1 and 2, 2026-10-05 and 10-06):
- the install: about 5 minutes with pip, which shows nothing while it works ("this takes about five minutes and
  looks frozen; it isn't"); `uv pip install` took 2 min 37 s and shows progress, so prefer it when uv is there;
- the warm-up before the plugin (below): about 5 minutes, the same libraries;
- the first ismail call after an install (`guide`): about a minute, while Python loads it the first time;
- the first sketch: about 2 minutes (1 min 43 s), all three about 5;
- without MCP, every CLI call: 15 to 20 seconds to start.

Pick the first route that fits:
- **In Claude Code or the desktop app's Code tab:** the plugin brings the tools and this skill in one step.
  1. **Warm it up first.** The plugin starts its server with uvx, which builds the same libraries the first time
     it runs, and a first start that takes minutes can time out. Run the exact command once yourself before the
     plugin lines, and tell them it is the five-minute step:
     `uvx --python 3.12 --from git+https://github.com/newsbubbles/ismail ismail --help`.
  2. **The plugin, with as little pasting as possible.** If a `claude` command runs in your shell
     (`claude --version`), add it yourself, and they paste nothing:
     `claude plugin marketplace add newsbubbles/ismail`, then `claude plugin install ismail@ismail`.
     Otherwise slash commands are typed by the person in the chat box. Give them both lines in one block, with one
     sentence: "These two lines add ismail to Claude: paste the first into the message box and press Enter, then
     the second."
     ```
     /plugin marketplace add newsbubbles/ismail
     /plugin install ismail@ismail
     ```
     On Claude Code 2.1.275 or newer one line does both: `/plugin install ismail --marketplace newsbubbles/ismail`
     (it asks them to confirm the source). An install in the chat box opens the plugin's page and asks where to
     install it: tell them to choose "Install for you".
  3. **The restart.** The songs folder (below) reaches ismail only when the app starts again, and so do the
     plugin's tools if they aren't listed yet. Do the songs folder first, so one restart covers both. Say so before
     it happens, once: "I'll ask you to quit Claude and open it again; this conversation will still be here." On
     Windows closing the window only hides it: "Quit Claude from the tray (right-click its icon by the clock,
     Quit), then open it again." On macOS: Claude, then Quit Claude, in the menu bar.
  4. **The Allow prompts.** The first time each ismail tool runs, the app asks whether to allow it. You told them
     the count at the start; at the first one say: "A box will ask to allow ismail. Press Allow (or Always allow,
     so it stops asking)."
- **Any MCP client:** register the server command
  `uvx --python 3.12 --from git+https://github.com/newsbubbles/ismail ismail mcp`, the way that client adds MCP
  servers, after warming it up as above.
- **Without MCP:** make a virtual environment in a folder of their choice and run
  `uv pip install "ismail[live] @ git+https://github.com/newsbubbles/ismail"` (or pip, which shows no progress).
  Then every op runs as `python -m ismail -p <project> <op> ...`, and each call takes 15 to 20 seconds to start.

**Make a home for their music**: a folder they can find again (Documents/ismail or Music/ismail). Tell them where
it is. Every song lives in its own subfolder there. Then point ismail at it with the `ISMAIL_SONGS` environment
variable, or ismail looks for songs next to the installed package: the first-session check (`guide`) and the
machine board (`machine`) won't see their songs. The board lives in `<ISMAIL_SONGS>/_machine` (or
`ISMAIL_MACHINE_DIR`), so every agent on this computer that uses the same songs folder shares one board.
- **Windows:** find Documents through the known-folder path, not `%USERPROFILE%\Documents`: with OneDrive backup
  on (the default on many new PCs) the Documents they see in Explorer is OneDrive\Documents. In PowerShell:
  ```
  $docs = [Environment]::GetFolderPath('MyDocuments'); New-Item -ItemType Directory -Force "$docs\ismail" | Out-Null; setx ISMAIL_SONGS "$docs\ismail"
  ```
  New processes see it: the app restart above picks it up. Tell them the folder the way Explorer shows it
  ("Documents, then ismail").
- **macOS and Linux:** `export ISMAIL_SONGS=~/Documents/ismail` in their shell profile; for an MCP client also put
  it in the server's `env` block, since desktop apps don't read shell profiles.

## 4. Prove it works, from their side

Call `guide`. Then make the smallest sound that proves the chain: `sketch` with their first words, or a 2-bar
project rendered with `mp3='also'`. Say that something is about to play, then open the file for them in their
default player: Windows `start "" "<file>.mp3"` (in PowerShell, `Invoke-Item "<file>.mp3"`), macOS
`open "<file>.mp3"`, Linux `xdg-open "<file>.mp3"`. It is
verified when they say they heard it, not when the render returns. If they hear nothing, check the volume, the
output device and the file before anything else.

## 5. When something fails

- Read the error and fix the cause. Retry once with the fix. Don't retry the same command unchanged.
- After two failed attempts at one step, tell them in one plain sentence what is blocked and the one thing they
  could do (or that you will take another route).
- Never ask them to read a log, edit a file, or type a command you could run.
- A step that failed or confused them is a finding: write it down, with the error and the fix, in the song's
  `HANDOFF.md` under "Setup". The maintainer reads every handoff, and the next person's setup gets better from
  it.

### Optional: the phone page

If they want to listen and talk back from their phone (`phone.md`), the phone and this computer need Tailscale,
signed in to the same account: install it on both (the person signs in; say what they will see), then
`phone_start` gives the address. Phones allow the microphone only on https pages, which Tailscale's address is.
Nothing is public.

## 6. Then the first session

`guide`'s FIRST SESSION block is the one opening: follow it, and ask nothing of your own before their first sound
(a recording is welcome, never required). Its first question carries whether they play or read music ("what is it
for, and do you play?"), so they answer two questions, not three. Pass that answer, verbatim, to
`guide(first_answer=...)`: it says whether to talk to them as a musician or in plain words, and you keep to it
(`user-experience.md`).

If `machine` says WAIT while you set up or render (an antivirus scan, an update), wait quietly: tell someone new
"the computer is busy, one moment" and never ask them to close a program. A person who knows their instrument is the best judge ismail can have: what
they hear and how they name it are the data.
