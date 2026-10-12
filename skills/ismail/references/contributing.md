# Contributing back, for someone who has never used git

ismail grows from what its users build. A person who made an instrument, a voice, a fix or a lesson that would
help others can send it to the project. They should not need to know what git, a fork or a pull request is. You
do that part. They decide whether to share, under what name, and press the buttons only they can press.

## In the conversation where it was made (the studio)

1. Run the inclusion review (`development.md`): is it general, does it work, and does every recording it was
   measured from have a licence that allows it (`ref/SOURCES.md`)?
2. Ask whether they would like to contribute it. Say in one sentence what that means: it becomes public in the
   ismail repository, credited to them, under the MIT licence for code.
3. Write it in the song's `HANDOFF.md`: what it is, the files, the evidence (their ear tests and the numbers), the
   sources, and the credit line they want (a name, a handle, or anonymous).
4. Start the contributor dev, because the studio session never turns into a dev. Where you can start a session (a
   new conversation, agent or task), start it with this line; where you cannot, give the person the line to paste:
   > Be the contributor dev. Contribute my <organ voice> from <the song folder> to ismail
   > (github.com/newsbubbles/ismail). Read skills/ismail/references/contributing.md first.

## In the contributor dev's conversation: route A, through GitHub

1. **GitHub is yours.** Set up and sign in to what you need as you would any tool. Ask the person only for what
   only they can do (an account they create, a code they approve), in one plain sentence when it comes up. They
   never need to know what a fork or a pull request is.
2. **The rest is yours too:**
   - fork the repository;
   - clone the fork into a new working folder (never inside their music folder);
   - make a branch;
   - add the contribution where the engine keeps its kind (`ismail/voices/<family>/` for a voice), with its
     credit in the file and a `CHANGELOG.md` entry;
   - run the tests that touch it;
   - commit as them with their GitHub no-reply address (never their personal email unless they say so);
   - push, and open the pull request with `gh pr create`: what it is, the evidence, and the sources.
3. **Tell them it is sent,** give them the link, and say what happens next: the project's maintainer reviews it, and
   GitHub emails them when someone comments or it is merged.

## Route B, without GitHub

If an account is too much today, pack it instead:
- a zip of the files;
- the `HANDOFF.md` section;
- their credit line.

Put the zip in their Documents and tell them its name. They send it to whoever pointed them to ismail, or attach
it to a new issue at https://github.com/newsbubbles/ismail/issues. The project's maintainer opens the pull request and
credits them. Their work lands either way.

## Never

- Song folders, takes and private references are theirs and stay private. Only what they chose to share goes in.
- No audio they don't have the rights to share (`ref/SOURCES.md` says what each file allows). A voice measured
  from a recording ships its measurements, not the recording.
- Never push to the main repository directly, never change other files beyond the contribution, and
  never put their email or real name in public without their yes.
