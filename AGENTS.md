# ismail, for AI agents

A person pointed you here ("set me up with ismail", a link to this repository). ismail is a DAW you drive with
text: you write music as notes, instruments and code, render it, and read the audio back as text. The person
judges by ear. Read this page, then the playbook it names.

**If you cannot run commands on this computer** (a plain chat in a browser or a phone app), say so in one sentence
and name what works: Claude Code, the Code tab in the Claude desktop app, Cursor or Codex
(`skills/ismail/references/setup.md` has the list). Do not explain skills, plugins or servers.

**Their words, not ours.** Never say skill, plugin, MCP, uv or server to the person, and never ask them to find,
install or understand "the skill", unless they used the word first. Say "the add-on that gives me ismail's tools"
and do it yourself. If a step needs them (a prompt to click, a restart), say what they will see, in plain words.

1. **The playbook** is `skills/ismail/SKILL.md`. Read it before making anything, and keep it loaded.
2. **If ismail's tools are not available to you yet, set it up yourself** with
   `skills/ismail/references/setup.md`. You do the installing. Ask the person only for what only they can do
   (their password, a "Yes" on a prompt, a code typed into a page), one step at a time, saying first what they
   will see.
3. **Call `guide`.** For someone who has made nothing with ismail yet, it opens with the first session, and that
   block is the one opening: follow it, and ask nothing before it (a recording is welcome, never required). Two
   sentences on what this is, at most two questions ("what is it for, and do you play?", then a mood or a
   reference if they have one), and sound within minutes (`sketch`).
4. **Their first answer decides your words.** `guide(first_answer=<their words>)` says whether they are a musician
   (they named an instrument they play, a style they trained in, or reading music) or want plain words, and you
   keep to it from then on (`skills/ismail/references/user-experience.md`).
5. **Giving back.** When something they made could help others (an instrument, a voice, a fix), ask whether
   they would like to contribute it. `skills/ismail/references/contributing.md` takes someone who has never used
   git or GitHub through it, or sends it back without GitHub.
6. **Changing ismail itself** happens only when the person asks: `skills/ismail/references/development.md`.

Never play sound through their speakers without saying so first, never delete their files, and never download a
large sample set without their yes.
