# Deployment Guide — PR Reviews Scorecard

Deployment target: **AWS EC2 t3.small** (~$17/month total)  
Stack: Ubuntu 24.04 · Nginx · FastAPI (uvicorn) · Streamlit · cron

---

## Architecture

```
EC2 (t3.small, Ubuntu 24.04)
│
├── Nginx (port 80/443)          ← SSL termination, reverse proxy
│     ├── /api  → FastAPI :8000
│     └── /     → Streamlit :8501
│
├── FastAPI (uvicorn, port 8000) ← customer API
├── Streamlit (port 8501)        ← internal UI
└── Cron (14:00 UTC / 6 AM PST) ← daily incremental scheduler
```

---

## Cost

| Resource | Cost |
|---|---|
| EC2 t3.small (2 GB RAM) | ~$15/month |
| EBS 20 GB storage | ~$2/month |
| Domain name (Namecheap) | ~$10/year |
| **Total** | **~$17/month** |

---

## Step 1 — Launch EC2

1. Go to AWS Console → EC2 → Launch Instance
2. **AMI:** Ubuntu 24.04 LTS
3. **Instance type:** t3.small
4. **Key pair:** create or select a `.pem` key — download and keep it safe
5. **Security group:** open inbound ports:
   - 22 (SSH)
   - 80 (HTTP)
   - 443 (HTTPS)
6. **Storage:** 20 GB gp3
7. Launch

---

## Step 2 — SSH in and install system dependencies

```bash
ssh -i your-key.pem ubuntu@<EC2-PUBLIC-IP>

sudo apt update && sudo apt install -y nginx python3-pip git unzip
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # activate uv in current shell
```

---

## Step 3 — Deploy the application

```bash
git clone <your-repo-url> /home/ubuntu/pr-scorecard
cd /home/ubuntu/pr-scorecard

uv sync

# Create and fill in credentials
cp .env.example .env
nano .env
```

`.env` must contain:
```
GITHUB_TOKEN=<GitHub Personal Access Token>
AZURE_OPENAI_API_BASE=<Azure OpenAI endpoint URL>
AZURE_OPENAI_API_KEY=<Azure OpenAI API key>
AZURE_OPENAI_API_VERSION=2024-02-01
ADMIN_SECRET=<strong random string — run: python3 -c "import secrets; print(secrets.token_hex(24))">
```

---

## Step 4 — Bootstrap PR data (run once)

```bash
cd /home/ubuntu/pr-scorecard
uv run python src/services/scheduler.py --mode bootstrap
```

This fetches all PRs from the last 2 years for all authors in `config.AUTHORS`. Safe to re-run if interrupted.

---

## Step 5 — systemd services

### FastAPI

```bash
sudo nano /etc/systemd/system/fastapi.service
```

```ini
[Unit]
Description=PR Scorecard FastAPI
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/pr-scorecard
EnvironmentFile=/home/ubuntu/pr-scorecard/.env
ExecStart=/home/ubuntu/pr-scorecard/.venv/bin/uvicorn src.api.rest:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Streamlit

```bash
sudo nano /etc/systemd/system/streamlit.service
```

```ini
[Unit]
Description=PR Scorecard Streamlit
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/pr-scorecard
EnvironmentFile=/home/ubuntu/pr-scorecard/.env
ExecStart=/home/ubuntu/pr-scorecard/.venv/bin/streamlit run src/ui/app.py --server.port 8501 --server.address 127.0.0.1 --server.runOnSave false
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Enable and start both

```bash
sudo systemctl daemon-reload
sudo systemctl enable fastapi streamlit
sudo systemctl start fastapi streamlit

# Verify they are running
sudo systemctl status fastapi
sudo systemctl status streamlit
```

---

## Step 6 — Nginx reverse proxy

```bash
sudo nano /etc/nginx/sites-available/pr-scorecard
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/pr-scorecard /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## Step 7 — Free SSL with Let's Encrypt

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

Certbot auto-renews the certificate. Verify renewal works:
```bash
sudo certbot renew --dry-run
```

---

## Step 8 — Cron for daily scheduler

```bash
crontab -e
```

Add this line (runs at 14:00 UTC = 6 AM PST):
```
0 14 * * * cd /home/ubuntu/pr-scorecard && .venv/bin/python src/services/scheduler.py --mode incremental >> /home/ubuntu/pr-scorecard/logs/scheduler.log 2>&1
```

Create the logs directory:
```bash
mkdir -p /home/ubuntu/pr-scorecard/logs
```

---

## Step 9 — Create your first API key

```bash
curl -X POST https://your-domain.com/api/admin/keys \
  -H "X-Admin-Secret: <your ADMIN_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"label": "customer-name"}'
```

Share the returned `key` value with the customer.

---

## Useful commands

```bash
# View live logs
sudo journalctl -u fastapi -f
sudo journalctl -u streamlit -f
tail -f /home/ubuntu/pr-scorecard/logs/scheduler.log

# Restart services after a code update
cd /home/ubuntu/pr-scorecard && git pull && uv sync
sudo systemctl restart fastapi streamlit

# Check service status
sudo systemctl status fastapi streamlit

# Revoke a customer API key
curl -X DELETE https://your-domain.com/api/admin/keys/<key> \
  -H "X-Admin-Secret: <your ADMIN_SECRET>"
```

---

## Updating the application

```bash
ssh -i your-key.pem ubuntu@<EC2-PUBLIC-IP>
cd /home/ubuntu/pr-scorecard
git pull
uv sync
sudo systemctl restart fastapi streamlit
```
