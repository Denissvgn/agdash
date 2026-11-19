# The Ubuntu survey container

An Ubuntu 24.04 container on a Mac with the four CLI harnesses installed: Claude
Code, Codex, Pi, and the Antigravity CLI (`agy`). It is for finding where each
harness keeps its records on Linux, what shows that a session is live, how much of
its thinking it writes down, and what says how it is signed in. The findings set
what the Linux adapters read.

The harness versions match the macOS survey (Claude Code 2.1.325, Codex 0.167.2,
Pi 1.0.2). `agy` is whichever version Google's release server named when the image
was built; `survey.sh versions` prints it.

**The host's home is never mounted.** The container's home is a Docker volume of
its own, `agdash-survey-home`. The harnesses inside cannot see or change the records
and sign-ins on the Mac, and the Mac's sign-ins are not copied in. You sign in to
each harness inside the container, once. The sign-ins stay in the volume until
`survey.sh reset` deletes it.

## Build

```bash
devtools/ubuntu-survey/survey.sh build
devtools/ubuntu-survey/survey.sh versions
```

## Sign in, once

Open a shell inside. It starts in `~/work/demo`, a folder of synthetic files:

```bash
devtools/ubuntu-survey/survey.sh shell
```

Each sign-in prints a link. Open it in the browser on your Mac, approve, and paste
back any code it shows. Nothing inside the container needs a browser.

| Harness | Command | Notes |
|---|---|---|
| Claude Code | `claude`, then pick the Claude account sign-in | It prints a link and asks for the code the page shows |
| Codex | `codex login --device-auth` | Device-code sign-in must be turned on first in ChatGPT's Security settings |
| Antigravity | `agy`, then the Google sign-in | It should print a link. If it waits for a browser instead, stop it with Ctrl-C and say so |
| Pi | `pi`, then set up the same model as on your Mac | Pi uses a local model, not a sign-in. From inside the container your Mac is `host.docker.internal`, so a server on the Mac's `127.0.0.1:<port>` is reached as `http://host.docker.internal:<port>` |

Sign in with the same kind of account you use on the Mac, so that the auth fields
compare.

## One session each

In `~/work/demo`, start each harness and give it the same short task:

> Read fruit.txt and tell me how many lines it has. Think it through first.

It reads a file (a tool call) and gives the model a reason to think, so the
records show tool calls and thinking. Let each harness answer, then leave it open
and idle. A second shell, `survey.sh shell` again, joins the same container to look
at the live signals while the sessions run. After that, quit each harness normally.

Use only the demo folder and prompts like the one above. The sessions are real
sessions under your accounts, and their files stay in the volume. Fixtures are made
from them only through the fixture kit, which replaces every prompt, reply, path and
identifier.

## Reset

```bash
devtools/ubuntu-survey/survey.sh reset
```

This deletes the volume: every sign-in and session inside. It asks first.
