# Deploying Questly

> **สรุปภาษาไทย** คู่มือนี้ deploy Questly บน VM ฟรีของ Oracle Cloud (Always Free,
> Ampere A1, region Singapore เดียวกับ Neon) แอปรันด้วย uvicorn **worker เดียว**
> ภายใต้ systemd และฟังแค่ `127.0.0.1:8000` ส่วนผู้ใช้เข้าผ่าน Cloudflare Tunnel ที่
> `https://questly.comtech.fun` จึงไม่ต้องเปิดพอร์ต 80/443 บน VM ความลับทั้งหมดอยู่ใน
> `/etc/questly/questly.env` บนเครื่องเท่านั้น ไม่อยู่ใน git ห้ามรัน alembic จาก
> เซิร์ฟเวอร์นี้ (Neon อยู่ที่ head แล้ว และ migration เป็นงานของ OPS task) ก่อนเปิดให้คนใช้
> ให้ทำ [checklist ก่อนเปิดใช้งาน](#pre-launch-checklist) ให้ครบทุกข้อ

This runbook puts one Questly process on an Oracle Cloud Always Free VM and
serves it at `https://questly.comtech.fun` through a Cloudflare Tunnel. Run
every command as a user with `sudo` on the VM unless a step says otherwise.

## 1. The VM

| Setting | Value |
| --- | --- |
| Provider | Oracle Cloud Infrastructure, Always Free |
| Home region | Singapore (`ap-singapore-1`), the same city as Neon `ap-southeast-1` |
| Shape | `VM.Standard.A1.Flex`, 1 OCPU, 6 GB memory |
| Image | Ubuntu 24.04, aarch64 |

1 OCPU and 6 GB is half of the Always Free Ampere allowance (2 OCPU and 12 GB
as of June 2026), so a second VM still fits.

Network:

- The subnet's security list allows ingress on TCP 22 (SSH) only. Do not open
  80 or 443: `cloudflared` dials out to Cloudflare, so nothing needs to reach
  the VM from outside.
- Log in with an SSH key. Turn password login off: set
  `PasswordAuthentication no` in `/etc/ssh/sshd_config`, then
  `sudo systemctl restart ssh`.

Two Oracle behaviours to expect:

- Creating an A1 instance in Singapore can fail with "Out of capacity". Retry
  later; capacity comes back.
- Oracle may reclaim an Always Free instance it considers idle. Keep the app
  running and watch the account's email for a reclaim notice.

## 2. Install the code

Ubuntu 24.04 ships Python 3.12, which is what `pyproject.toml` asks for.

```bash
sudo apt update && sudo apt install -y git curl
sudo useradd --system --create-home --home-dir /var/lib/questly \
  --shell /usr/sbin/nologin questly
sudo -u questly -H bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

sudo mkdir -p /opt/questly && sudo chown questly:questly /opt/questly
sudo -u questly -H git clone -b dev \
  https://github.com/03376124-SDD-2-1-69-SEC-1/Questly.git /opt/questly
cd /opt/questly
sudo -u questly -H /var/lib/questly/.local/bin/uv sync --frozen --no-dev
```

If the repository is private, add a read-only deploy key for the `questly`
user and clone `git@github.com:03376124-SDD-2-1-69-SEC-1/Questly.git` instead.
Do not use a personal access token on the server.

The home directory is under `/var/lib`, not `/home`, because the service runs
with `ProtectHome=true`, which hides `/home` from it.

## 3. Secrets

Create `/etc/questly/questly.env`, readable by root and the `questly` group
only. The values below are placeholders. Put the real values in this file on
the VM and nowhere else: never in git, a chat or a ticket.

```bash
sudo mkdir -p /etc/questly
sudo install -o root -g questly -m 0640 /dev/null /etc/questly/questly.env
sudo nano /etc/questly/questly.env
```

```bash
APP_ENV=production
BASE_URL=https://questly.comtech.fun

DATABASE_URL=postgresql+psycopg://PLACEHOLDER_USER:PLACEHOLDER_PASSWORD@PLACEHOLDER-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require

R2_ENDPOINT_URL=https://PLACEHOLDER_ACCOUNT_ID.r2.cloudflarestorage.com
R2_BUCKET_NAME=PLACEHOLDER_BUCKET
R2_ACCESS_KEY_ID=PLACEHOLDER
R2_SECRET_ACCESS_KEY=PLACEHOLDER

OPENROUTER_API_KEY=PLACEHOLDER

EMAIL_BACKEND=resend
EMAIL_FROM=Questly <noreply@PLACEHOLDER_VERIFIED_DOMAIN>
RESEND_API_KEY=PLACEHOLDER

ONSCREEN_VERIFY_DOMAINS=kmitl.ac.th

JUDGE0_URL=https://ce.judge0.com
JUDGE0_AUTH_TOKEN=
```

What each group does, and what happens when it is wrong:

- `APP_ENV=production` hides `/docs`, `/redoc` and `/openapi.json`, refuses the
  `stub` email backend, and requires `BASE_URL`.
- `DATABASE_URL` must start with `postgresql+psycopg://`, or the app does not
  start.
- The four `R2_*` variables are required; the app does not start without them.
  `.env.example` also lists `R2_ACCOUNT_ID` and `R2_PUBLIC_URL`, which the app
  does not read.
- `OPENROUTER_API_KEY` turns on generation and PDF ingestion. Without it both
  fall back to stubs.
- `EMAIL_FROM` must use a domain verified in Resend, or Resend refuses to mail
  `@kmitl.ac.th`.
- Optional, with defaults in `src/questly/config.py`: `MAX_UPLOAD_SIZE_BYTES`,
  `GENERATION_MODEL`, `GENERATION_MODEL_ALLOWLIST`, `EMBEDDING_MODEL`.

### Judge0

`JUDGE0_URL=https://ce.judge0.com` is the public, shared Judge0 CE instance.
It is fine for a demo, but every Run and Submit sends the Student's code and
the Test Case inputs to a third party, outside our infrastructure, and the
shared instance is rate-limited. Leave `JUDGE0_AUTH_TOKEN` empty for it.

Do not plan to self-host Judge0 on this VM: its official images target x86_64
and the VM is ARM (aarch64).

## 4. Database migrations

Do not run `alembic` from this server. The shared Neon database is already at
the head revision, and migrations stay an OPS task (AGENTS.md "Do not do these
without a task saying so").

## 5. The service

`/etc/systemd/system/questly.service`:

```ini
[Unit]
Description=Questly
After=network-online.target
Wants=network-online.target

[Service]
User=questly
Group=questly
WorkingDirectory=/opt/questly
EnvironmentFile=/etc/questly/questly.env
ExecStart=/opt/questly/.venv/bin/uvicorn questly.main:app --host 127.0.0.1 --port 8000 --workers 1 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now questly
curl -s http://127.0.0.1:8000/health
```

**Why exactly one worker.** Several things live in the memory of one process
and are not shared between workers:

- the progress of a running generation job (ADR-0010),
- the verification links shown on screen (ADR-0013),
- the rate-limit counts for login, sign-up and resend,
- the queue of member emails waiting to be sent.

With two workers, a Student could be rate-limited in one and not the other, and
an Instructor's generation progress would vanish whenever the next request
landed on the other worker. Queued member emails are lost when the process
stops or restarts, so restart outside busy publishing times.

`--proxy-headers --forwarded-allow-ips 127.0.0.1` makes uvicorn take the client
address from `X-Forwarded-For` only when the request comes from `cloudflared`
on the same machine. The rate limits count per that address.

## 6. Cloudflare Tunnel

The zone `comtech.fun` is already on Cloudflare.

1. Install `cloudflared` from Cloudflare's apt repository (see Cloudflare's
   "Downloads" page for the current commands for Ubuntu 24.04 arm64).
2. In the Cloudflare dashboard, open Zero Trust, then Networks, then Tunnels.
   Create a tunnel named `questly` with the `cloudflared` connector. Copy the
   install command it shows and run it on the VM:
   `sudo cloudflared service install <TOKEN>`. This installs `cloudflared` as a
   systemd service. The token is a secret: do not paste it anywhere else.
3. On the tunnel, add a public hostname: `questly.comtech.fun`, service type
   `HTTP`, URL `127.0.0.1:8000`. Cloudflare creates the DNS record.
4. In the `comtech.fun` zone, set SSL/TLS encryption mode to **Full**.

Check: `systemctl status cloudflared`, then open `https://questly.comtech.fun`.

## 7. Update, logs and rollback

Update to the latest `dev`:

```bash
cd /opt/questly
sudo -u questly -H git pull
sudo -u questly -H /var/lib/questly/.local/bin/uv sync --frozen --no-dev
sudo systemctl restart questly
```

Logs:

```bash
journalctl -u questly -f
journalctl -u cloudflared -f
```

Roll back to an earlier commit:

```bash
cd /opt/questly
sudo -u questly -H git log --oneline -10
sudo -u questly -H git checkout <previous sha>
sudo -u questly -H /var/lib/questly/.local/bin/uv sync --frozen --no-dev
sudo systemctl restart questly
```

`git checkout <sha>` leaves the checkout on a detached HEAD. To go back to
updates, run `sudo -u questly -H git checkout dev` before the next `git pull`.

## Pre-launch checklist

Do every item before anyone outside the team gets the URL.

- [ ] Rotate the Neon `neondb_owner` password in the Neon console, and put only
      the new one in `/etc/questly/questly.env`.
- [ ] Log in, open DevTools, and confirm the `questly_session` cookie is
      `Secure`, `HttpOnly` and `SameSite=Lax`.
- [ ] `https://questly.comtech.fun/docs` returns 404 (also `/redoc` and
      `/openapi.json`).
- [ ] `curl -I https://questly.comtech.fun/login` shows
      `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
      `Referrer-Policy`, `Permissions-Policy`, `Content-Security-Policy` and
      `Strict-Transport-Security`.
- [ ] 11 quick logins with a wrong password from one IP: the 11th gets 429.
- [ ] A request from a second network (phone on mobile data) is not limited while the first IP is. If both share one bucket, uvicorn is not honouring the forwarded client address and one person can lock everyone out; recheck `--proxy-headers` and `--forwarded-allow-ips`.
- [ ] Know the limits are per public IP: students on one campus NAT share the buckets (login 10 per 5 min, sign-up 5 per hour), so a whole lab signing up at once can see 429.
- [ ] A Student who is not a Member gets 404 on another Classroom's
      Assignment URL, and a non-Admin gets 403 on `/admin`.
- [ ] An Assignment titled `<img src=x onerror=alert(1)>` renders as text.
- [ ] On T-06, a renamed `.exe` and a file over 20 MB are both refused.
- [ ] Sign-up with a Gmail address receives a real email, and sign-up with an
      `@kmitl.ac.th` address shows the on-screen "ยืนยันอีเมล" button.
- [ ] Record the grade from `https://securityheaders.com` for
      `https://questly.comtech.fun`.
