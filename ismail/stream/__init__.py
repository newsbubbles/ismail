"""Live streaming for agents: one set out to several sites at once, and every site's chat in and out.

The stream server (`python -m ismail.stream.server`, started by stream_start) is one local process on 127.0.0.1 that
holds three things:

- **Venues.** A venue is one channel on one site: {name, platform, channel, chat, output}. The configs live in
  ~/.ismail/stream/venues.json and hold no secrets. A venue's keys (its stream key, its chat tokens) live in
  ~/.ismail/stream/keys/<venue>.json, which only the person writes, with `python -m ismail.stream login <venue>` and
  `python -m ismail.stream key <venue>` in a terminal. No op ever returns a key; ops say whether one is there.
- **Chat.** Each chat venue has an adapter (adapters.py: twitch, youtube, kick, owncast, and `fake`, a local page
  where anyone on this machine types as a viewer). Every message, from every venue, lands in one numbered log
  (chat.jsonl) as {n, ts, kind, venue, platform, user, text, id}, so a reply always knows where it goes. Agents read
  it with chat_watch (blocks until something arrives, like phone_watch) and answer with chat_say (one venue, or all).
- **Output** (output.py). The live engine's master, fed on a clock with silence across engine restarts, plus a
  picture (a visualizer drawn by ffmpeg from the sound, a still or a captured window, with text lines over it), encoded
  once, then copied to every venue that streams. Each destination is its own small process, so one site dropping
  never stops the others, and it is reconnected on its own.
- **Requests** (request_queue.py). Chat's requests in one queue the MC fills and the DJ plays from, an append-only log
  (requests.jsonl) that needs no server, so both sessions see one line.

Going live on a public venue is the person's yes, every time: stream_go asks for their words and logs them.
"""
