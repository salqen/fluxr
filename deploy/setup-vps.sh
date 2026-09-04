#!/usr/bin/env bash
# Bootstrap / update fluxr na VPS (Ubuntu 24.04). Idempotentné, spúšťať ako root.
#   DOMAIN=fluxr.bropri.sk bash setup-vps.sh
# Nerobí nič s webserverom — vhost + SSL sa riešia cez CyberPanel (viď README).
set -euo pipefail
DOMAIN="${DOMAIN:-fluxr.bropri.sk}"
REPO="${REPO:-https://github.com/salqen/fluxr.git}"
BASE=/opt/fluxr

id fluxr &>/dev/null || useradd --system --home "$BASE" --shell /usr/sbin/nologin fluxr
mkdir -p "$BASE/data"

DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv git >/dev/null

if [ -d "$BASE/app/.git" ]; then
  git -C "$BASE/app" pull --ff-only
else
  git clone --depth 1 "$REPO" "$BASE/app"
fi

[ -x "$BASE/venv/bin/python" ] || python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install -q --upgrade pip
"$BASE/venv/bin/pip" install -q -r "$BASE/app/requirements.txt"

# .env sa vytvorí len raz — tajomstvá (META_APP_SECRET, ANTHROPIC_API_KEY) doplň ručne
if [ ! -f "$BASE/.env" ]; then
  cat > "$BASE/.env" <<ENV
# fluxr — produkcia ($DOMAIN). Súbor číta len root a služba fluxr.
META_APP_ID=996438119804252
META_APP_SECRET=
REDIRECT_URI=https://$DOMAIN/auth/callback
BASE_URL=https://$DOMAIN
SECRET_KEY=$(openssl rand -hex 32)
AGENT_TOKEN=$(openssl rand -hex 16)
ANTHROPIC_API_KEY=
DATA_DIR=$BASE/data
PORT=5000
ENV
  echo "[setup] vytvorený $BASE/.env — doplň META_APP_SECRET"
fi

chown -R fluxr:fluxr "$BASE/data"
chown root:fluxr "$BASE/.env" && chmod 640 "$BASE/.env"

install -m 644 "$BASE/app/deploy/fluxr.service" /etc/systemd/system/fluxr.service
systemctl daemon-reload
systemctl enable --quiet fluxr
systemctl restart fluxr

sleep 2
if curl -fsS http://127.0.0.1:5000/health >/dev/null; then
  echo "[setup] fluxr beží na 127.0.0.1:5000 ($(systemctl is-active fluxr))"
else
  echo "[setup] CHYBA — služba neodpovedá:"; journalctl -u fluxr -n 30 --no-pager; exit 1
fi
