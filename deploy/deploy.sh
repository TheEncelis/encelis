#!/usr/bin/env bash
# Install / update the app from /srv/encelis. Run as root after the code is in place.
set -euo pipefail
APP=/srv/encelis
cd "$APP"

chown -R encelis:www-data "$APP"
chmod 640 "$APP/.env"
chmod 750 "$APP/var"

# Python environment
sudo -u encelis python3 -m venv "$APP/.venv"
sudo -u encelis "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u encelis "$APP/.venv/bin/pip" install -q -r requirements.txt

# Django reads .env itself (python-dotenv); never `source` it in a shell.
run() { sudo -u encelis "$APP/.venv/bin/python" manage.py "$@"; }

# SQLite: WAL mode lets readers and the writer work concurrently.
sudo -u encelis sqlite3 "$APP/var/db.sqlite3" "PRAGMA journal_mode=WAL;" >/dev/null

run migrate --noinput
run collectstatic --noinput -v 0
run ensure_admin
run check --deploy --fail-level ERROR

# Service and web server
install -m 644 deploy/encelis.service /etc/systemd/system/encelis.service
# Install the site only once: certbot adds the HTTPS parts to it afterwards, and
# overwriting it on later deploys would silently switch HTTPS off.
[ -f /etc/nginx/sites-available/encelis ] || install -m 644 deploy/nginx-encelis.conf /etc/nginx/sites-available/encelis
ln -sf /etc/nginx/sites-available/encelis /etc/nginx/sites-enabled/encelis
# Nginx must be able to read static files and reach the socket.
chmod 755 "$APP" "$APP/var"
find "$APP/var/staticfiles" "$APP/static" -type d -exec chmod 755 {} +
find "$APP/var/staticfiles" "$APP/static" -type f -exec chmod 644 {} +
chmod 750 "$APP/var/backups" 2>/dev/null || true
chmod 640 "$APP/var/db.sqlite3"* "$APP/var/dev_secret_key" 2>/dev/null || true

# Daily database backup at 03:30, keeping two weeks.
cat > /etc/cron.d/encelis-backup <<CRON
30 3 * * * encelis cd $APP && $APP/.venv/bin/python manage.py backup_db --keep 14 >> $APP/var/backup.log 2>&1
CRON
chmod 644 /etc/cron.d/encelis-backup

systemctl daemon-reload
systemctl enable encelis
systemctl restart encelis
nginx -t
systemctl reload nginx
echo "DEPLOY-DONE"
