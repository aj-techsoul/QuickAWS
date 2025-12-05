# Clean Server
cd ~ || exit

# 1. Stop and remove compose-managed containers (if any)
cd ~/app 2>/dev/null || true
sudo docker-compose down --remove-orphans || true

# 2. Remove any containers matching the app_* name pattern (force)
sudo docker ps -a --format '{{.Names}}' | grep '^app-' || true
sudo bash -c 'for c in $(docker ps -a --format "{{.Names}}" | grep "^app-" || true); do docker rm -f "$c" || true; done'

# 3. Remove volumes for the app (volume name starts with app_)
sudo bash -c 'for v in $(docker volume ls -q | grep "^app_" || true); do docker volume rm -f "$v" || true; done'

# 4. Remove images that were created locally for this project (look for "app-" tag/names)
sudo bash -c 'for i in $(docker images --format "{{.Repository}}:{{.Tag}} {{.ID}}" | grep "^app-" | awk "{print \$2}" || true); do docker rmi -f "$i" || true; done'

# 5. Remove leftover images we pulled for QuickAWS services (optional - safe)
#    Only remove these if you want a truly clean pull next time.
sudo docker rmi -f nginx:stable-alpine php:8.1-fpm-alpine mariadb:10.5 phpmyadmin/phpmyadmin adminer:latest mywebsql/mywebsql:latest || true

# 6. Remove the app directory entirely (backup first if you care)
#    If you want to keep a backup, move instead of remove.
if [ -d "$HOME/app" ]; then
  mv "$HOME/app" "$HOME/app.backup.$(date +%s)"
  echo "Moved existing ~/app to ~/app.backup.*"
fi
