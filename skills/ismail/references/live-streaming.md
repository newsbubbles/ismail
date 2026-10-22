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
   call with everything: `mode` (cqt, spectrum, waves, scope, none), `colors`, `background` or `image` or `window`
   (the title of a window to capture: a page you draw, the VR stage's mirror), `area`. Set the text lines, which
   change live with no blip: `stream_text('title', ...)`, `('note', ...)` ("type !request in chat"), `('now', ...)`
   (the piece). `chat_overlay` puts the newest chat line in a corner by itself.
3. Ask the person: "Go live on Twitch and YouTube now?" Only their answer counts, in their words:
   `stream_go(['twitch', 'yt'], approved='<their words>')`. It is logged with the time. A `file` venue needs no yes.
4. `stream_look` right after, and every few minutes: each site's state (live, or reconnecting with the site's own
   error), MB sent, and a preview frame to Read. A site that drops reconnects on its own; the others carry on.
5. `stream_end()` stops every site (or `stream_end(['kick'])` one). Chat stays connected for the goodbyes.

## Chat: the MC

Run the watcher in the background and re-arm it FIRST after every wake:
`python -m ismail chat_watch "who=MC"`. It returns every venue's lines as JSON, each with its `n`, venue, user and
text. Answer with `chat_say(text, reply_to=<n>)`: it goes back to that site, threaded where the site threads.
`chat_say(text, venue='all')` speaks to every chat at once (a welcome, a "last track").

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

The MC never touches the mixer: chat, the picture and helper renders share the machine, so they stay off the
mixer's cores.

## Games and commands

Chat commands are plain chat lines; the MC reads them from `chat_watch` and decides. Keep a few, and put them on
screen with `stream_text('note', ...)`:
- `!request <style or song>`: a request for the DJ, queued, one per person per 10 minutes.
- `!vote <a|b>`: the MC opens a vote ("next: faster or deeper? !vote a / !vote b"), counts it for a minute, and
  hands the DJ the result.
- `!love`: counts like the person's phone love tap: the DJ keeps what plays a little longer.

## When something fails

- `stream_status` says, per venue, whether chat is reading, waiting (YouTube waits for the channel to go live), or
  needs a login, and the line the person types for it.
- A site that keeps reconnecting: `stream_look` shows its own error (a wrong key, the site down). Say it plainly
  and leave the other sites live.
- YouTube's quota for the day is used up: its chat comes back after midnight Pacific; the stream itself carries on.
