# Cloudflare quick tunnel with a watchdog (see tunnel.sh).
FROM cloudflare/cloudflared:latest AS cloudflared
FROM alpine:3.20
RUN apk add --no-cache ca-certificates curl
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared
COPY tunnel.sh /tunnel.sh
ENTRYPOINT ["/bin/sh", "/tunnel.sh"]
