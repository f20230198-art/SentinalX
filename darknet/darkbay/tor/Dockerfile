# Tor image for DarkBay (starts as root to fix folder permissions, then drops to debian-tor)

FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tor ca-certificates util-linux \
 && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/lib/tor/sentinelx_forum \
 && chown -R debian-tor:debian-tor /var/lib/tor \
 && chmod 700 /var/lib/tor/sentinelx_forum

COPY torrc /etc/tor/torrc
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 9050

# Stays root so entrypoint can fix permissions first
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
