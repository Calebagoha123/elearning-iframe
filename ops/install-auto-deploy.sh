#!/usr/bin/env sh
set -eu

if [ "$(id -u)" != 0 ]; then
    echo "Run this installer as root on the production VM." >&2
    exit 1
fi

repo=/opt/elearning-iframe
if [ ! -f "$repo/.env.production" ]; then
    echo "The existing production environment is missing at $repo/.env.production." >&2
    exit 1
fi

install -m 644 "$repo/ops/elearning-deploy.service" /etc/systemd/system/elearning-deploy.service
install -m 644 "$repo/ops/elearning-deploy.timer" /etc/systemd/system/elearning-deploy.timer
systemctl daemon-reload
systemctl enable --now elearning-deploy.timer
systemctl start elearning-deploy.service
systemctl list-timers elearning-deploy.timer --no-pager
