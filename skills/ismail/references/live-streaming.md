# Live streaming: one set to several sites, and every site's chat

A live set goes out to Twitch, YouTube, Kick or any site that takes RTMP, all at once, with a picture drawn from
the music. Every site's chat comes into one place, and you answer each person where they wrote. Going live is the
person's yes, every time.

## What runs where

- **The stream server** (`stream_start`): one local process. It holds every venue's chat connection and, while
  live, the output. It sends nothing to any site until `stream_go`.
- **Venues** (`venue_set`, `venue_list`, `venue_remove`): one channel on one site each. The config holds no secrets.
- **Keys** stay with the person. They type them in a terminal, once per venue:
  - `python -m ismail.stream login <venue>`: signs the account in for chat replies and events (Twitch and YouTube
    show a short code to type on any device; Kick opens the browser). The first time, it says how to make the site's
    app and asks for its client id.
  - `python -m ismail.stream key <venue>`: the stream key, hidden while typed.
  You never see, type, read or ask for a key. `venue_list` says which are in place and gives the exact line the
  person types for each one missing. Say what they will see ("a code to type on the Twitch site").

## Setting up (once)

1. `venue_set(name='practice', platform='fake')`: a local practice chat at `http://127.0.0.1:8875/fake`, where
   anyone on this machine types as a viewer. `script=[{after_s, user, text}]` adds a scripted test viewer.
2. The real ones: `venue_set(name='twitch', platform='twitch', channel='<login>')`,
   `venue_set(name='yt', platform='youtube', channel='@handle')`, `venue_set(name='kick', platform='kick',
   channel='<slug>')`. Any other site: `platform='rtmp'` with its `ingest` address (output only, no chat).
   A recording of every set: `venue_set(name='archive', platform='file', channel='<folder>/set.flv')`.
3. `venue_list`, then ask the person for what it lists, one venue at a time.
4. Stream only, no chat: `chat=False` needs only the key; add the login later for chat. For example
   `venue_set(name='yt', platform='youtube', channel='@handle', chat=False, ingest='rtmp://a.rtmp.youtube.com/live2')`,
   then the person types `python -m ismail.stream key yt`, and `stream_go` works. Twitch chat can be read with no
   login at all (replies need it).

What each site's chat can do here:

| site | read | reply | events | notes |
|---|---|---|---|---|
| Twitch | realtime | yes, threaded | follows, subs, gifts, cheers, raids, channel points | reads anonymously with no login (no replies) |
| YouTube | realtime (streamList), or every 10 s | yes, `@name` in front | Super Chats, members, gifts | reading costs API quota: streamed, YouTube does not publish the cost (counted as one list call per open, about hourly); polled, 5 units a read with `reader='poll'` (`poll_s`, 10 by default); chat exists only while the channel is live |
| Kick | realtime | yes, threaded | follows, subs, gifts | its read path is unofficial and can break; replies use Kick's official API |
| Owncast | realtime | yes | - | self-hosted |
| fake | realtime | yes | scripted | for practice and tests |

## Going live

1. A live set plays (`references/live.md`). The stream carries the newest engine's master; across an engine
   restart it carries silence and the picture, so a reset never drops a site.
2. Set the picture first (`stream_visual`), because a change while live is a 2-3 s blip on every site. Use one
   call with everything: `mode` (cqt, spectrum, waves, scope, page, none), `colors`, `background` or `image` or
   `window` (the title of a window to capture: the VR stage's mirror), `area`. `page` is the visual page below. Set
   the text lines, which
   change live with no blip: `stream_text('title', ...)`, `('note', ...)` ("type !request in chat"), `('now', ...)`
   (the piece). `chat_overlay` puts the newest chat line in a corner by itself.
3. Ask the person: "Go live on Twitch and YouTube now?" Only their answer counts, in their words:
   `stream_go(['twitch', 'yt'], approved='<their words>')`. It is logged with the time. A `file` venue needs no yes.
4. `stream_look` right after, and every few minutes: each site's state (live, or reconnecting with the site's own
   error), MB sent, and a preview frame to Read. A site that drops reconnects on its own; the others carry on.
5. `stream_end()` stops every site (or `stream_end(['kick'])` one). Chat stays connected for the goodbyes.

## The visual page

`stream_visual(mode='page')` streams a page the stream server draws from the set: the master's level and 16 bands
(40 Hz to 16 kHz, log-spaced: 0-3 sub and bass, 4-7 low mids, 8-11 mids and presence, 12-15 air), the engine's
beat, and each track's notes as they sound. It reads the engine's local stream and its clock and onsets, about once a
second; it never touches the mixer. You set what it draws with numbers, and it changes live with no blip:

- `stream_scene(layers=[...], background=['#07080c', '#1a1030'], trails=0.35)` replaces the layers;
  `replace=False` adds to them. Kinds: `bars`, `ring`, `line` (bands), `pulse`, `particles`, `hits` (a light per
  track), `text`, `grid`. Positions and sizes are fractions of the frame. `drive` picks what moves a layer: `level`,
  `beat`, `band:<i>`, `bands:<lo>-<hi>`, or `track:<name>` (a flash on that track's notes). A wrong field comes back
  with the fields that layer takes.
- `stream_page()` gives the page's address, the layers, and what the page sees now (beat, level, bands, the notes
  just played). Open the address to look at it; it makes no sound. `?debug=1` adds a readout.
- Keep the page's own text clear of the stream's text lines (the title top left, the piece bottom left): they draw
  on top.
- On air, the page opens in its own small browser window (its own profile, muted) that the encoder captures, cropped
  to the page. Leave that window where it is; covering it is fine. It has no resize border, and while live
  anything that changes its size is undone within a second or two, so the picture always fills the frame. One
  page window at a time: an open one is reused, never a second opened.
- If the encoder starts three times in a row with no picture (a window capture that gives no frames), the stream
  stops itself, with no more restarts, and `stream_status` names the cause and the next call: usually
  `stream_visual(mode='cqt')` (no window) or `capture='gdi'`, then `stream_go` again (a public venue takes the
  person's yes again).

## Chat: the MC

Run the watcher in the background and re-arm it FIRST after every wake:
`python -m ismail chat_watch "who=MC"`. It returns every venue's lines as JSON, each with its `n`, venue, user and
text. Answer with `chat_say(text, reply_to=<n>)`: it goes back to that site, threaded where the site threads, or
with `@name` in front where it doesn't, so don't open the line with their name yourself.
`chat_say(text, venue='all')` speaks to every chat at once (a welcome, a "last track").
A new MC's first watch starts 5 minutes back, so the room's opening is there. After catching up with `chat_read`,
arm the watcher with `since=<its last>` so no line is answered twice.

Manners, which viewers feel at once:
- Greet people by name, and the first message of someone new before anything else. Thank every follow, sub, gift,
  cheer and raid by name, in one line each. Gather a burst into one line ("thanks Ana, Bo and Cy for the follows").
- Never leave a question unanswered for more than a minute or two. "Good question, one sec" counts.
- Short lines. No walls of text, no repeats (the server refuses a line said in the last 30 s), no links unless
  asked. YouTube takes at most 200 characters.
- Every site gets the same welcome: don't send people from one site to another.
- Never say anything the person hasn't cleared about themselves, their place or their plans. The privacy guard
  checks each line.
- Never argue. Ignore trolls; the person decides on bans.

## The DJ and the MC

A stream with chat works as two roles, in two sessions, each with its own helpers:
- **The DJ** keeps the music going, by the rules in `references/live.md`, and is the only one who calls `live_*`.
- **The MC** reads chat, greets, answers, runs the games, and passes requests to the DJ as one short line each
  (a style, a song name, "change", "more of this"), gathered at phrase ends, never mid-build. A vote's result is
  fine; raw chat is not. During an engine reset, the MC fills the gap in chat, never with sound.

## The request queue

The MC and the DJ share one queue of requests, kept on disk, so it works across their two sessions and a restart.
- **The MC** puts each request in as it arrives: `request_add(who='<their name>', what='deep house',
  venue='<venue>')`, a few words, not the chat line. It returns the place in line; tell the viewer ("you're 3rd").
  When someone asks "when does mine play?", `request_where(who)` gives the place and, once a few requests have
  played, about how many minutes. `request_list` shows the whole line.
- **The DJ** reads `request_next` at each phrase end, never mid-build. When it starts, `request_mark(id,
  'playing')` (the one before is marked done, and the stream's `now` line says whose request it is); `request_mark(id, 'skipped', note='why')` for one that does not fit,
  so the MC can tell the viewer. The DJ never reads raw chat.
- **Credit.** When a song is made from what chat asked for, `request_credits(ids=[...], path='<song folder>')` adds
  each viewer (name, venue, what they asked for) to its CREDITS.md. Ask them in chat first ("can I credit you for
  the cello idea?"), and credit only those who say yes.

The MC never touches the mixer: chat, the picture and helper renders share the machine, so they stay off the
mixer's cores.

## Callouts and panels: what viewers see of the set

The visual page carries text over its picture, live, with no blip: callouts for a moment, and panels that keep
themselves up to date from the stream's own data. They draw on the page, so the stream shows them in
`mode='page'`. Both take raw numbers: positions and sizes are fractions of the frame, colours `'#rrggbb'`.

- **A callout** is one timed line: `stream_callout(text='drop in 8 bars', anchor='center', duration_s=4,
  enter='pop', who='DJ')`. It comes in, stays `duration_s` (its animations included) and goes. Place it with
  `anchor` (top, top-left, top-right, left, center, right, lower-third, bottom, bottom-left, bottom-right) or `x`,
  `y` (the point the box is pinned to); `w` is its widest. `style={font, size, color, weight, italic, background,
  background_opacity, outline, outline_color, shadow, shadow_color, align, padding, radius, opacity}`: `font` is a
  name (sans, serif, mono, condensed, display, rounded, or arial, impact, georgia, consolas and a few more) or a
  .ttf/.otf/.woff/.woff2 file under the ismail folder or the stream home; `size` is the font's height as a
  fraction of the frame (0.06). `enter` and `exit`: none, fade, pop, slide-left, slide-right, slide-up, slide-down
  (in from that side and back out to it).
- **Callouts take turns.** One shows at a time in each slot (the anchor's name, or `slot='<your own>'`), so two
  never overlap: a new one waits, higher `priority` (0-9) first, then first asked, and starts the moment the one
  before ends. Nothing cuts the one showing; take it down with `stream_callout(clear=True, id=<n>)`.
  `clear=True` alone takes down all of them, `clear=True, slot='top'` one slot. Every reply says what shows and
  what waits, with ids; `stream_callout()` with nothing asks just that. The same words twice in one slot are
  refused.
- **A panel** is bound to data: `stream_panel('now')` (the request playing and "requested by @name", or the
  stream's `now` line when no request plays), `('requests')` (the waiting line in play order, with places),
  `('chat')` (the newest chat lines, names, text and site; `venues=[...]` for some), `('credits')` (the viewers
  whose requests played). One of each kind; `rows`, `title` ('' for none), `empty` (a line for when there is
  nothing: "type !request <style> in chat"; without it an empty panel hides), placement and `style` as callouts,
  plus `accent` for the title and names. Fields you leave out keep their value. `on=False` takes one down. Panels
  are kept across a server restart; callouts are not.
- **Did it land?** `stream_page()` ends with what an open page last drew ("the page draws: callouts #3; panels
  requests, chat"). Open `http://127.0.0.1:8875/visual?debug=1` to look; the readout counts them.

Who calls them:
- **The MC**: the panels (put them up before going live: `now`, `requests` with an `empty` line, and `chat` if the
  room is friendly) and callouts for people: a welcome to raiders, "thanks Ana for the sub", "requests open". Use
  `who='MC'`.
- **The DJ**: callouts for the music, at the moment: "drop in 8 bars", "back to back: jungle", "last track". Short
  ones (3-5 s), in their own slot (`anchor='center'` or `slot='dj'`) so they never wait behind the MC's. Use
  `who='DJ'`.

Keep them off the stream's own text lines (title top left, the piece bottom left), which draw over the page. With
the `now` panel up, the `now` line says the same: `request_mark(..., on_screen=False)` keeps it to the panel.
Viewers' chat lines show as they wrote them (the privacy guard checks your own words, not theirs): take the chat
panel down if chat turns bad. Use `stream_text` for a line that stays (the title, "type !request in chat"),
`stream_scene` text layers for words that move with the music, and callouts for a moment.

## Games and commands

Chat commands are plain chat lines; the MC reads them from `chat_watch`. Keep a few, and put them on screen with
`stream_text('note', ...)`:
- `!request <style or song>` and `!vote <id>` are handled by rule: hand each batch from `chat_watch` to
  `chat_commands(lines)` and say the replies it returns with `chat_say(text, reply_to=...)`. It queues the request,
  refuses it with the reason (one waiting per person, one per 10 minutes, too long), or counts it as a vote when one
  like it is waiting. A vote moves a request up the line. It acts on a line once, so handing it the same batch
  again is safe. The rules are per venue: `venue_rules(venue='*')` shows them; change them before the set, not
  during it.
- `!vote <a|b>`: a vote of your own ("next: faster or deeper? !vote a / !vote b"). `chat_commands` leaves these to
  you in `left`; count them for a minute and hand the DJ the result.
- `!love`: counts like the person's phone love tap: the DJ keeps what plays a little longer. `chat_commands`
  counts it for the request that plays (`request_next` shows the count) and gives no reply. One `!vote` or `!love`
  per viewer per minute (`venue_rules(reward_s=)`), so nobody can spam them.

## When something fails

- `stream_status` says, per venue, whether chat is reading, waiting (YouTube waits for the channel to go live), or
  needs a login, and the line the person types for it.
- The stream stopped itself (`stream_status` says "the encoder failed 3 starts in a row"): read the cause and make
  the call it names, then `stream_go` again. It never retries on its own after that.
- A site that keeps reconnecting: `stream_look` shows its own error (a wrong key, the site down). Say it plainly
  and leave the other sites live.
- YouTube's quota for the day is used up: its chat comes back after midnight Pacific; the stream itself carries on.
