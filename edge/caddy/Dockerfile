# Caddy with a DNS provider module for ACME DNS-01 (works for LAN-only hosts with a public domain).
# Module versions verified to build against Caddy v2.10.2:
#   github.com/caddy-dns/cloudflare@v0.2.4
#   github.com/caddy-dns/duckdns@v0.5.0
ARG CADDY_VERSION=2.10.2
FROM caddy:${CADDY_VERSION}-builder AS build
ARG DNS_MODULE=github.com/caddy-dns/cloudflare@v0.2.4
RUN xcaddy build --with ${DNS_MODULE}

FROM caddy:${CADDY_VERSION}
COPY --from=build /usr/bin/caddy /usr/bin/caddy
