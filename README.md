# ismail for Claude Code

This branch holds only the Claude Code plugin for [ismail](https://github.com/newsbubbles/ismail), an open-source
(MIT) DAW your agent runs itself: it writes music as notes, instruments and mix settings, renders it on your
computer and reads every render back as text. No AI audio model makes the sound.

The plugin carries the ismail skill and starts the ismail server from PyPI (`uvx --python 3.12 --from ismail==<version> ismail mcp`),
so the code lives in the main branch and in the PyPI release, not here.

Install in Claude Code:

    /plugin install ismail --marketplace newsbubbles/ismail

Source, docs and issues: https://github.com/newsbubbles/ismail
