#!/bin/sh
# Fix ownership of the bind-mounted hidden service dir at runtime, then drop
# privileges to debian-tor. Bind mounts on Docker Desktop (Windows/macOS) come
# in as root-owned regardless of what the image set, and Tor refuses to start
# if HiddenServiceDir isn't owned by the running user with mode 700.
set -e

HS_DIR=/var/lib/tor/sentinelx_forum

mkdir -p "$HS_DIR"
chown -R debian-tor:debian-tor "$HS_DIR"
chmod 700 "$HS_DIR"

# Tor's own data dir (cached descriptors, etc.) — also needs to be writable.
chown -R debian-tor:debian-tor /var/lib/tor

exec setpriv --reuid=debian-tor --regid=debian-tor --init-groups \
    tor -f /etc/tor/torrc
