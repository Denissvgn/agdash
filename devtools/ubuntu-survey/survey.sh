#!/bin/sh
# The Ubuntu survey container, run from the host:
#
#     devtools/ubuntu-survey/survey.sh build      build the image
#     devtools/ubuntu-survey/survey.sh versions   the version of each harness inside
#     devtools/ubuntu-survey/survey.sh shell      a shell inside, to sign in and run sessions
#     devtools/ubuntu-survey/survey.sh live       what shows the running sessions are live
#     devtools/ubuntu-survey/survey.sh read [OUT]  survey the home read-only with agdash
#     devtools/ubuntu-survey/survey.sh reset      delete the home volume: sign-ins and sessions
#
# The container's home is the Docker volume agdash-survey-home. Nothing from the host's
# home is mounted: the harnesses inside never read or change the host's records or
# sign-ins, and what they write stays in the volume until `reset`.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
IMAGE=agdash-ubuntu-survey:24.04
READER=agdash-survey-reader
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
      # A second shell in the running container, for watching a session from outside;
      # never one left running on an image that has since been rebuilt.
      if [ "$(docker inspect -f '{{.Image}}' "$NAME")" != "$(docker image inspect -f '{{.Id}}' "$IMAGE")" ]; then
        echo "The running $NAME container is from an older image. Exit every shell in it, then run this again." >&2
        exit 1
      fi
      exec docker exec -it "$NAME" bash -l
    fi
    run -it --name "$NAME" "$IMAGE" bash -l
    ;;
  live)
    docker exec -i "$NAME" sh -s < "$HERE/live.sh"
    ;;
  read)
    # agdash from this checkout reads the volume, mounted read-only, with no network.
    OUT=$(mkdir -p "${2:-$HERE/../../.runtime/ubuntu-survey}" && cd "${2:-$HERE/../../.runtime/ubuntu-survey}" && pwd)
    docker build -q -t "$READER" -f "$HERE/Dockerfile.reader" "$HERE/../../backend" > /dev/null
    docker run --rm --network none -e HOME=/home/surveyor \
      --mount "type=volume,source=$VOLUME,target=/home/surveyor,readonly" \
      --mount "type=bind,source=$HERE,target=/survey,readonly" \
      --mount "type=bind,source=$OUT,target=/out" \
      "$READER" sh /survey/read.sh
    ;;
  reset)
    printf 'Delete %s, with every sign-in and session inside? [y/N] ' "$VOLUME"
    read -r answer
    [ "$answer" = y ] && docker volume rm "$VOLUME"
    ;;
  *)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
