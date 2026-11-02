"""The hub: one way for agents to talk to people, on every interface (ledger:M429, songs/_migration/HUB_PLAN.md).

The hub server (`python -m ismail.hub.server`, started by hub_start) is one local process on 127.0.0.1 that holds:

- **Places.** A place is one way to reach people: a Discord bot, later Telegram, the phone page and VR. Its config
  ({name, platform, public, guest_text, only}) lives in ~/.ismail/hub/places.json with no secrets; its token lives in
  ~/.ismail/hub/keys/<place>.json, which only the person writes (`python -m ismail.hub token <place>`). Each place has
  an adapter (adapters/) that says what it can show (its caps: buttons, forms, files, edits, threads).
- **People.** One people file maps a person's accounts to one name ("sam": their Discord id, ...), linked by a one-time
  pairing code or by the person in a terminal. Everyone else is a guest, named by the platform's own id, never by
  text they type.
- **One log.** Every message, button press and form answer from every place, numbered, stamped with who sent it and
  what their words may do (trust: owner, mod, guest; use: word, suggestion, request, ignored). Agents read it with
  hub_watch and answer with hub_say, which shows text, files, buttons and forms the way each place can, or falls back
  to typed replies, and says which.
- **Policy.** The owner's paired accounts (the hub.owner setting) carry their word. A guest's text is never an
  instruction: per place it is ignored, a suggestion, or a request. hub_pause stops every public place at once.

The phone (ismail/phone), the VR stage (ismail/stage) and stream chat (ismail/stream) keep their own ops and files
unchanged; the plan folds them in later, one at a time, behind their tests. Until then the hub copies the owner's
phone and VR lines into its log, read only (bridges.py), and can send to the phone page through the phone_* ops.
"""
