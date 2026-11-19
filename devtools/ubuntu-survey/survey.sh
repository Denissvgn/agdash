#!/bin/sh
# The Ubuntu survey container, run from the host:
#
#     devtools/ubuntu-survey/survey.sh build      build the image
#     devtools/ubuntu-survey/survey.sh versions   the version of each harness inside
#     devtools/ubuntu-survey/survey.sh shell      a shell inside, to sign in and run sessions
#     devtools/ubuntu-survey/survey.sh reset      delete the home volume: sign-ins and sessions
#
# The container's home is the Docker volume agdash-survey-home. Nothing from the host's
# home is mounted: the harnesses inside never read or change the host's records or
# sign-ins, and what they write stays in the volume until `reset`.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
IMAGE=agdash-ubuntu-survey:24.04
VOLUME=agdash-survey-home
NAME=agdash-survey

run() { # docker run options, then the command
  docker run --rm --hostname "$NAME" \
    --mount "type=volume,source=$VOLUME,target=/home/surveyor" \
    --add-host host.docker.internal:host-gateway \
    "$@"
}

case "${1:-}" in
  build)
    docker build --pull -t "$IMAGE" "$HERE"
    ;;
  versions)
    run "$IMAGE" sh -c '
      . /etc/os-release && echo "$PRETTY_NAME ($(uname -m))"
      echo "node $(node --version)"
      echo "claude $(claude --version)"
      echo "codex $(codex --version)"
      echo "pi $(npm ls -g --depth=0 @earendil-works/pi-coding-agent | sed -n "s/.*@//p")"
      cat /etc/agy-version'
    ;;
  shell)
    if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
      # A second shell in the running container, for watching a session from outside.
      exec docker exec -it "$NAME" bash -l
    fi
    run -it --name "$NAME" "$IMAGE" bash -l
    ;;
  reset)
    printf 'Delete %s, with every sign-in and session inside? [y/N] ' "$VOLUME"
    read -r answer
    [ "$answer" = y ] && docker volume rm "$VOLUME"
    ;;
  *)
    sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
