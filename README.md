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
- **Live streaming** (`stream_*` tools, set up by you with your own accounts): your set's audio and video, and the
  chat replies you approve, go to YouTube, Twitch or Kick. Viewers' chat (their names and messages) is read and kept
  in a log on your computer (`chat.jsonl` in the stream folder).
- **The phone page** loads its fonts from Google Fonts.

Source, docs and issues: https://github.com/newsbubbles/ismail · [Privacy](#privacy)
