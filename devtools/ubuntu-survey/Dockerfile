# An Ubuntu machine with the four CLI harnesses, for surveying where each keeps its
# records on Linux. Built and run by ./survey.sh; see README.md.
#
# The harness versions match the ones in use on the Mac, so the two surveys compare.
# The home folder is a Docker volume of its own: nothing from the host's home is
# mounted, so the harnesses here never see or change the host's records.
FROM ubuntu:24.04

ARG NODE_VERSION=22.23.4
ARG CLAUDE_CODE_VERSION=2.1.326
ARG CODEX_VERSION=0.169.0
ARG PI_VERSION=1.5.2

ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git xz-utils \
      procps lsof psmisc file sqlite3 jq python3 less \
 && rm -rf /var/lib/apt/lists/*

# Node.js, from nodejs.org, checked against its published SHA-256.
RUN set -eu; \
    case "$(uname -m)" in aarch64) arch=arm64 ;; x86_64) arch=x64 ;; *) exit 1 ;; esac; \
    name="node-v${NODE_VERSION}-linux-${arch}.tar.xz"; \
    cd /tmp; \
    curl -fsSLO "https://nodejs.org/dist/v${NODE_VERSION}/${name}"; \
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" | grep " ${name}\$" | sha256sum -c -; \
    tar -xJf "${name}" -C /usr/local --strip-components=1 --exclude CHANGELOG.md --exclude LICENSE --exclude README.md; \
    rm "${name}"; \
    node --version

# Claude Code, Codex and Pi, at the versions in use on the Mac. Pi needs no install scripts.
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" "@openai/codex@${CODEX_VERSION}" \
 && npm install -g --ignore-scripts "@earendil-works/pi-coding-agent@${PI_VERSION}" \
 && npm cache clean --force

# The Antigravity CLI, `agy`: the same steps as Google's installer
# (https://antigravity.google/cli/install.sh): read the release manifest, download,
# and check the SHA-512 it names. It goes in /usr/local/bin, where the person using it
# cannot write, so its background self-update keeps the version this image was built
# with.
RUN set -eu; \
    case "$(uname -m)" in aarch64) arch=arm64 ;; x86_64) arch=amd64 ;; *) exit 1 ;; esac; \
    base=https://antigravity-cli-auto-updater-974169037036.us-central1.run.app; \
    manifest=$(curl -fsSL "$base/manifests/linux_${arch}.json"); \
    url=$(printf '%s' "$manifest" | jq -er .url); \
    sha512=$(printf '%s' "$manifest" | jq -er .sha512); \
    printf '%s' "$manifest" | jq -r '"agy " + .version' > /etc/agy-version; \
    cd /tmp; \
    curl -fsSL -o agy.download "$url"; \
    echo "${sha512}  agy.download" | sha512sum -c -; \
    case "$url" in \
      *.tar.gz*) tar -xzf agy.download antigravity && install -m 0755 antigravity /usr/local/bin/agy && rm antigravity ;; \
      *) install -m 0755 agy.download /usr/local/bin/agy ;; \
    esac; \
    rm agy.download

# The person who runs the sessions. Their home is the volume, which starts as a copy
# of this one: a folder of synthetic files for every session to work on.
RUN useradd --create-home --shell /bin/bash surveyor \
 && mkdir -p /home/surveyor/work/demo \
 && printf '%s\n' "# Demo" "A folder for the survey's sessions. Nothing here is real." > /home/surveyor/work/demo/README.md \
 && printf '%s\n' apples pears plums cherries > /home/surveyor/work/demo/fruit.txt \
 && git -C /home/surveyor/work/demo init -q \
 && chown -R surveyor:surveyor /home/surveyor
USER surveyor
WORKDIR /home/surveyor/work/demo
CMD ["bash", "-l"]
