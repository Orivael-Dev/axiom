# projectx.orivael.dev deploy

Static SPA deploy of the React dashboard (`sites/projectx/`, Vite build) to the
Hetzner box, served by Caddy `file_server` — same pattern as the other orivael sites.

## One-command redeploy
```bash
bash deploy/projectx/deploy.sh
```
Builds `sites/projectx` and uploads `dist/` to `/opt/sites/projectx/` on the box.
No Caddy reload needed (static files). Override the host with `PROJECTX_HOST=root@<ip>`.

## Manual steps (what the script does)
```bash
cd sites/projectx
npm install
npm run build                       # -> dist/ (index.html + hashed assets)
ssh root@178.156.205.89 "mkdir -p /opt/sites/projectx && rm -rf /opt/sites/projectx/assets"
scp -r dist/index.html dist/assets root@178.156.205.89:/opt/sites/projectx/
```

## Caddy block (already in `deploy/firewall/Caddyfile`)
```
projectx.orivael.dev {
    encode gzip
    root * /opt/sites/projectx
    try_files {path} /index.html          # SPA fallback (client routes)
    file_server
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options    "nosniff"
        X-Frame-Options           "SAMEORIGIN"
        Referrer-Policy           "strict-origin-when-cross-origin"
        Content-Security-Policy   "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' https:"
        -Server
    }
    log { output stdout
          format json }
}
```
Reload after editing the Caddyfile: `docker exec axiom-caddy caddy reload --config /etc/caddy/Caddyfile`.

## DNS
Namecheap A record `projectx` → `178.156.205.89` (already set).

## Notes
- `try_files {path} /index.html` makes client-side routes work on refresh.
- Vite emits content-hashed assets; the script clears `/opt/sites/projectx/assets`
  before upload so stale bundles don't pile up.
