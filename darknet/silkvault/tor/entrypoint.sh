#!/bin/sh
# Fix hidden-service folder permissions (Tor needs owner + mode 700), then run Tor as debian-tor
set -e

HS_DIR=/var/lib/tor/silkvault_forum

mkdir -p "$HS_DIR"
chown -R debian-tor:debian-tor "$HS_DIR"
chmod 700 "$HS_DIR"

# Tor's own data dir (cached descriptors, etc.) — also needs to be writable.
chown -R debian-tor:debian-tor /var/lib/tor

exec setpriv --reuid=debian-tor --regid=debian-tor --init-groups \
    tor -f /etc/tor/torrc
