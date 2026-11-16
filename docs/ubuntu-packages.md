# The Ubuntu packages

agdash runs on Ubuntu 22.04 LTS and later, on amd64 and arm64. There are three
ways to install it:

| Package | What it holds | For |
|---|---|---|
| `agdash_<version>_<arch>.deb` | The service and the `agdash` command, with the UI built in | Every host, servers included |
| `agdash-desktop_<version>_<arch>.deb` | The window and the tray item; needs the package above | A desktop |
| `agdash_<version>_<arch>.AppImage` | The window, the tray item and the service in one file | A desktop, without installing packages |

They are built on Ubuntu 22.04, so they run on that release and every later one.
CI builds all three for both architectures and installs the service package in
fresh Ubuntu 22.04, 24.04 and 26.04 containers with no display.

## A server, with no display

```bash
sudo apt install ./agdash_<version>_<arch>.deb
agdash install
```

`agdash install` starts the service as your user, now and at every login, as a
systemd user unit. The package itself starts nothing and enables nothing for
anyone. A user service stops when you log out of the server unless lingering is
on for your account; agdash does not change that. To keep it running:

```bash
loginctl enable-linger "$USER"
```

To see the UI, run `agdash open` on the server. It prints a link that carries the
service's local token, after the `#`: a browser never sends that part to a
server, and the UI moves it out of the address as it opens. Anyone with the link
can read your sessions, so keep it to yourself. On your own computer, forward the
port, then open the link:

```bash
ssh -N -L 8765:127.0.0.1:8765 <server>
```

The service still listens on `127.0.0.1` only. It serves the UI's files without
the token, since they hold no data, and refuses every `/api` request without it.

`agdash status` says whether the service runs and what it holds; `agdash uninstall`
stops it starting at login and keeps your data.

## A desktop

```bash
sudo apt install ./agdash_<version>_<arch>.deb ./agdash-desktop_<version>_<arch>.deb
```

agdash then appears in the applications menu. The window attaches to the
service, or starts it, and the tray item shows the number of live sessions.
Quitting the window leaves the service running; `agdash install` starts the
service at login.

The AppImage needs no installation. The service it carries is copied to
`~/.local/share/agdash/bin/agdash` the first time it runs, so the service keeps
working after the AppImage closes. To start it at login:

```bash
~/.local/share/agdash/bin/agdash install
```

The desktop package and the AppImage need WebKitGTK 4.1 and the AppIndicator
library, which Ubuntu's desktop installs have.
