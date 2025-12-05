#!/usr/bin/env bash
set -e
# Usage: ./bootstrap.sh git@github.com:yourorg/yourrepo.git /home/ec2-user/app

REPO_SSH_URL="$1"
DEST_DIR="${2:-/home/ec2-user/app}"

if [ -z "$REPO_SSH_URL" ]; then
  echo "Usage: $0 git@github.com:org/repo.git [dest_dir]"
  exit 1
fi

# ensure git installed
if ! command -v git >/dev/null 2>&1; then
  echo "git not found — attempting to install"
  if [ -f /etc/os-release ] && grep -q "amzn" /etc/os-release; then
    sudo yum install -y git
  else
    sudo apt-get update && sudo apt-get install -y git
  fi
fi

mkdir -p "$DEST_DIR"
cd "$DEST_DIR"

if [ -d .git ]; then
  echo "Repo already exists at $DEST_DIR — doing a git pull"
  git pull
else
  echo "Cloning $REPO_SSH_URL into $DEST_DIR"
  git clone "$REPO_SSH_URL" .
fi

# Make sure provisioning script is executable
if [ -f provision.py ]; then
  chmod +x provision.py
fi

echo "Bootstrap complete. Next: run $DEST_DIR/provision.py (python3) to provision."
