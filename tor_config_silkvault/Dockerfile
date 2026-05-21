# Tor daemon image for SilkVault (SentinelX synthetic darknet forum #2).
# A second, independent Tor instance from tor_config/ so SilkVault publishes
# its own distinct .onion address.
#
# Runs as root long enough to fix bind-mount ownership at startup, then drops
# to the debian-tor user via setpriv before exec'ing tor itself.

FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tor ca-certificates util-linux \
 && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/lib/tor/silkvault_forum \
 && chown -R debian-tor:debian-tor /var/lib/tor \
 && chmod 700 /var/lib/tor/silkvault_forum

COPY torrc /etc/tor/torrc
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 9050

# NOTE: stays as root so entrypoint can chown the bind mount; entrypoint then
# drops privileges via setpriv before exec'ing tor.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
