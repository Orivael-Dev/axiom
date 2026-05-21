# Re:Search Engine — beta hosting

Deploy the AXIOM research console for invited beta testers behind:

- **TLS** (Caddy auto-issues a Let's Encrypt cert for `RESEARCH_HOST`)
- **Basic auth** at the edge (only invited testers can see the page)
- **Optional bearer token** for defense-in-depth on `/api/*`
- **Container isolation** from the firewall stack — beta load can't
  knock over `firewall.orivael.dev`

## One-time setup on the Hetzner box

```bash
ssh root@<your-box>
cd /opt/axiom                              # or wherever you cloned
git pull origin claude/test-axiom-security-KgUcQ

# DNS first — point research.<your-domain> at this box.
# Then:
cd deploy/research
cp .env.example .env
chmod 600 .env

# Fill in three required values:
#   AXIOM_MASTER_KEY   = $(openssl rand -hex 32)   # back this up out-of-band
#   RESEARCH_HOST      = research.<your-domain>
#   DEEPSEEK_API_KEY   = sk-...
# Plus the basic-auth pairs (see below).
$EDITOR .env
```

## Generate basic-auth credentials per beta tester

One pair per invited tester:

```bash
docker run --rm caddy:2-alpine caddy hash-password
# (it prompts for the password — paste a strong random one)
# It prints something like: $2a$14$abcde...
```

Then assemble into `BETA_AUTH_HASHES` in `.env` on ONE line:

```bash
BETA_AUTH_HASHES="alice $2a$14$alicehash bob $2a$14$bobhash carol $2a$14$carolhash"
```

Each pair is `<username> <bcrypt-hash>`, whitespace-separated.

Send each tester their username + the plaintext password (1Password share,
encrypted email, signal — never paste in the same channel you'll send
the URL).

## Bring it up

```bash
docker compose --profile tls up -d --build
```

That builds the image, pulls Caddy, provisions a TLS cert, and starts
serving at `https://${RESEARCH_HOST}`.

Verify:

```bash
docker compose ps
docker compose logs -f research caddy | grep -iE 'started|error|certificate'

# Healthcheck (no auth required):
curl https://${RESEARCH_HOST}/api/health
# → {"status":"ok",...}

# Try without credentials (should 401):
curl -sI https://${RESEARCH_HOST}/
# → HTTP/2 401  +  www-authenticate: Basic realm="restricted"

# With credentials (should 200):
curl -sI https://${RESEARCH_HOST}/ -u alice:correct-horse-battery-staple
# → HTTP/2 200
```

## Beta tester instructions (copy-paste into your invite email)

> The Re:Search Engine beta lives at
> **https://research.YOUR-DOMAIN/**
>
> Username: `alice`
> Password: `<paste here>`
>
> Type a research question, hit Run. Every result carries a green
> ✓ SIGNED · VERIFIED ribbon showing the underlying event token's id
> and verification status. The full doc is at the 📖 Instructions
> link at the top of the page.
>
> Feedback: <your feedback channel>

## Operations

### Pause the beta (drop the URL)

```bash
docker compose down
```

Caddy releases the port, the URL stops responding. Bring it back with
`docker compose --profile tls up -d`.

### Rotate one tester out

Edit `.env`, remove their `<user> <hash>` pair from
`BETA_AUTH_HASHES`, then:

```bash
docker compose up -d caddy        # reloads Caddyfile, no restart of the app
```

### Tail logs

```bash
docker compose logs -f research          # FastAPI + signed-event activity
docker compose logs -f caddy             # access log + cert renewal
```

Caddy logs are JSON; pipe through `jq` to filter by tester:

```bash
docker compose logs caddy --tail=2000 | \
  jq -r 'select(.request.uri == "/api/research") |
         "\(.ts) \(.request.headers.Authorization?[0] // "—") \(.request.uri)"'
```

(The bearer token leaks here if `AXIOM_RESEARCH_TOKEN` is set —
that's deliberate so you can spot misuse from the access log; rotate
the token regularly.)

### See who's actually using it

The research server already writes a signed ledger entry per
invocation. Read it from inside the container:

```bash
docker compose exec research \
  python -c "from axiom_exoskeleton_ledger import read_ledger; \
             [print(e.timestamp_utc, e.use_case, e.token_id) \
              for e in read_ledger()[-20:]]"
```

Or browse `https://${RESEARCH_HOST}/ledger` (gated by basic-auth, same
credentials as the console).

### Update the code

```bash
cd /opt/axiom
git pull
cd deploy/research
docker compose up -d --build research      # rebuilds + restarts ONLY research
```

Caddy keeps running through the rebuild — no auth-state hiccup for
in-progress tester sessions.

## Cost control during the beta

The biggest risk is a runaway tester burning through LLM credits.
A few cheap defenses:

1. **Use DeepSeek-chat (V3) not deepseek-reasoner (R1).** V3 is 4×
   cheaper per million tokens.
2. **Set a hard daily spending cap at the LLM provider.** DeepSeek's
   dashboard has a daily-limit toggle.
3. **Monitor token spend** via the existing ledger:
   ```bash
   docker compose exec research python -c "
   from axiom_exoskeleton_ledger import read_ledger
   total_in = sum(e.input_tokens for e in read_ledger()[-200:])
   total_out = sum(e.output_tokens for e in read_ledger()[-200:])
   print(f'last 200 runs: {total_in:,} in / {total_out:,} out')
   "
   ```

A solo founder + 5–10 beta testers at typical question volume should
cost **$10–30/month** on DeepSeek's API. Switch to local Ollama on a
GPU host when the beta proves the loop is worth it.

## Pure-local dev (no auth gate, no TLS)

Skip Caddy entirely; expose port 8765 to localhost only:

```yaml
# in docker-compose.yml under services.research:
ports:
  - "127.0.0.1:8765:8765"
```

```bash
docker compose up research
# Open http://127.0.0.1:8765/
```

Useful for testing the build before exposing on the public box.

## Security checklist before letting testers in

- [ ] `AXIOM_MASTER_KEY` is set, 64 hex chars, backed up out-of-band
- [ ] `BETA_AUTH_HASHES` has one pair per real tester, no shared accounts
- [ ] DNS A/CNAME record points at the box; firewall allows 80 + 443
- [ ] Caddy successfully provisioned the TLS cert
  (`docker compose logs caddy | grep -i certificate`)
- [ ] `/api/health` returns 200 without credentials
- [ ] `/` returns 401 without credentials
- [ ] `/` returns 200 with `alice:<password>` (replace alice with a real tester)
- [ ] DeepSeek / NIM API key has a daily spending cap set at the provider
- [ ] You can `docker compose down` and the URL goes 503/timeout immediately
