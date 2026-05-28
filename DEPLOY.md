# Deploying the_felt behind Cloudflare Tunnel

Target: `https://felt.magech.ai` → container on the R620.

## 1. Push the repo

From your local machine (or any agent with push):

```bash
cd /Users/rishi/Projects/the_felt
git init
git branch -m main
git remote add origin git@github.com:azrael92/the-felt.git
git add .
git commit -m "Initial: the_felt poker probability trainer"
git push -u origin main
```

## 2. On the R620, clone + build

```bash
cd /opt        # or wherever OpenClaw lives
git clone https://github.com/azrael92/the-felt.git
cd the_felt
cp .env.example .env
# Edit .env, set ANTHROPIC_API_KEY (optional — falls back to template prose without it)
docker compose up -d --build
# Verify
curl -sS http://127.0.0.1:8000/api/lessons | head -2
docker logs -f the_felt
```

The container binds to `127.0.0.1:8000` only, so it's not reachable from the LAN — Cloudflare Tunnel will proxy it.

## 3. Cloudflare Tunnel ingress

Add to your existing `cloudflared` config (`/etc/cloudflared/config.yml` or wherever the magech tunnel lives):

```yaml
tunnel: <your-existing-tunnel-id>
credentials-file: /etc/cloudflared/<tunnel-id>.json

ingress:
  - hostname: felt.magech.ai
    service: http://127.0.0.1:8000
    originRequest:
      noTLSVerify: true
      # WebSocket support is on by default in cloudflared, but make it explicit:
      proxyType: ""
      connectTimeout: 30s
      tlsTimeout: 10s
      tcpKeepAlive: 30s
      keepAliveConnections: 10
      keepAliveTimeout: 90s

  # ... your existing ingress rules for magech.ai etc ...

  - service: http_status:404
```

Then create the DNS record:

```bash
sudo cloudflared tunnel route dns <tunnel-name-or-id> felt.magech.ai
sudo systemctl restart cloudflared
```

Verify: `curl -I https://felt.magech.ai/` should return `200 OK`. Open in a browser and the trainer loads.

## 4. WebSocket sanity check

The hand loop uses WebSockets. From any machine with `wscat` or similar:

```bash
wscat -c wss://felt.magech.ai/ws
> {"type":"join","v":1,"data":{"user_name":"Probe","seats":6,"stack_bb":100,"sb":5,"bb":10}}
```

You should see a `joined` event back within a second, followed by `hand_start`.

## 5. Updates

On `main` push, you can either:

- Manually pull + rebuild on the R620: `git pull && docker compose up -d --build`
- Or set up a GitHub Actions webhook → SSH → pull, mirroring whatever pattern OpenClaw uses for its agents

## 6. Cost + abuse

- **Anthropic spend**: the coach LLM polish is the only paid call. Each user decision triggers ~1 polish call (~200 tokens output). At 1k visitors × 30 decisions × $0.005 = ~$150/mo at worst case. Most visitors won't play 30 hands. Set `ANTHROPIC_API_KEY=""` to disable LLM polish entirely if cost becomes a concern — the deterministic fallback prose is still good.
- **DB growth**: SQLite stores ratings + decisions + hand histories. With `THE_FELT_PUBLIC_DEMO=1` set, each visitor gets an ephemeral user (in-memory ratings, no decision logging) so the DB stays small.
- **Rate limiting**: not implemented. If the_felt.magech.ai gets popular, add Cloudflare rate-limiting rules in the zone settings (free tier supports 10k requests/10min per IP).
