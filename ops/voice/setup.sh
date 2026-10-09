#!/bin/bash
# One-time setup for the AI phone agent's secrets. Writes /etc/techdeva/voice
# (root-only): bridge.token (random; shared by Asterisk and the voice bridge)
# and trunk.conf (from trunk.conf.example, for you to fill in). Prints no secret.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 1; }
D=/etc/techdeva/voice
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -d /etc/techdeva ] || install -d -m 700 /etc/techdeva     # never changes an existing folder
install -d -m 700 "$D"          # root-only: both containers run as root and read it read-only
if [ ! -s "$D/bridge.token" ]; then
  ( umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$D/bridge.token" )
  echo "bridge token: created"
else
  echo "bridge token: already there"
fi
if [ ! -s "$D/trunk.conf" ]; then
  install -m 600 "$HERE/trunk.conf.example" "$D/trunk.conf"
  echo "trunk.conf: created from the example. Now type in the line's details yourself:  nano $D/trunk.conf"
else
  echo "trunk.conf: already there (not changed)"
fi
grep -q CHANGE_ME "$D/trunk.conf" && echo "NOTE: trunk.conf still has CHANGE_ME values: fill them in before starting Asterisk." || true
