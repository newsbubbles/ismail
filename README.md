# ismail for Claude Code

This branch holds only the Claude Code plugin for [ismail](https://github.com/newsbubbles/ismail), an open-source
(MIT) DAW your agent runs itself: it writes music as notes, instruments and mix settings, renders it on your
computer and reads every render back as text. No AI audio model makes the sound.

The plugin carries the ismail skill and starts the ismail server from its PyPI release, with every dependency pinned by hash in `uv.lock` (`uvx --python 3.12 ismail==<version> mcp`, run locked by Claude Code),
so the code lives in the main branch and in the PyPI release, not here.

Install in Claude Code:

    /plugin install ismail --marketplace newsbubbles/ismail

## Privacy

What leaves your computer, and when.

ismail runs on your computer and has no service of its own: nothing is sent to us. It contacts other services only
for these, each when you ask for it:

- **Sample libraries:** downloaded from GitHub (sfzinstruments) and the University of York (SADIE II) after your yes.
- **Reference tracks:** when you name a song or give a link (YouTube, SoundCloud), the agent searches for it and
  downloads its audio with yt-dlp into your song folder, after asking once to install yt-dlp.
- **Live streaming** (`stream_*` tools, set up by you with your own accounts): your set's audio and video, and the
  chat replies you approve, go to YouTube, Twitch or Kick. Viewers' chat (their names and messages) is read and kept
  in a log on your computer (`chat.jsonl` in the stream folder).
- **The phone page** loads its fonts from Google Fonts.

## For reviewers

Notes on the two policy holds.

**Runs a pinned uvx package.** The server is `uvx --python 3.12 ismail==0.4.3 mcp`. `ismail` on PyPI is this
project: each release is built from its tag in https://github.com/newsbubbles/ismail by the release workflow
(`.github/workflows/release.yml`) and uploaded with PyPI trusted publishing, after an approval in a protected
environment. `uv.lock` beside the manifest pins every dependency to an exact version and hash (97 entries). We do
not vendor the source: the package is about 300 files plus numpy, scipy and other compiled wheels, past the
plugin's 512-file limit.

**Uses a credential from the user's machine.** The hold cites `skills/ismail/references/stage.md`. That file is
instructions for the agent and reads nothing: "pass" there means a room-building step (`pass_script`, `passes`),
not a password. The plugin itself reads no credential. The one place ismail uses credentials is live streaming
(`stream_*` tools in the package): the person types their own stream key or signs in with the platform's own login
page, the tokens stay on their computer, and they go only to the platform they belong to (YouTube, Twitch, Kick).

Source, docs and issues: https://github.com/newsbubbles/ismail · [Privacy](#privacy)
