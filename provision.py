#!/usr/bin/env python3
"""
QuickAWS provision.py
A robust, verbose provisioner:
- detects OS/arch
- installs Docker + Docker Compose (best effort)
- generates strong passwords and .env
- writes docker-compose.yml from templates
- places index.php into www/
- starts docker compose
- writes README_SECURE.txt (chmod 600)
- logs to provision.log

Usage:
  python3 provision.py                 # interactive
  NONINTERACTIVE=1 PROFILE=php python3 provision.py   # non-interactive
"""
from __future__ import annotations
import os
import sys
import time
import json
import secrets
import string
import shutil
import subprocess
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parent
LOGFILE = ROOT / "provision.log"
ENVFILE = ROOT / ".env"
README_SECURE = ROOT / "README_SECURE.txt"
WWW_DIR = ROOT / "www"
PHP_DIR = ROOT / "php"
NGINX_CONF_DIR = ROOT / "nginx" / "conf.d"
COMPOSE_FILE = ROOT / "docker-compose.yml"

# Simple logger that appends to LOGFILE and prints to stdout
def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {msg}"
    print(line)
    try:
        with open(LOGFILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def run(cmd: str, check=True, env=None):
    log(f"> {cmd}")
    res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    if res.stdout:
        for l in res.stdout.splitlines():
            log("  " + l)
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd} (exit {res.returncode})")
    return res

def which(binname: str) -> bool:
    return shutil.which(binname) is not None

def detect_os() -> Dict[str,str]:
    data = {"ID": "", "NAME": "", "VERSION_ID": "", "ARCH": os.uname().machine}
    if Path("/etc/os-release").exists():
        for line in open("/etc/os-release", "r"):
            if "=" in line:
                k,v = line.strip().split("=", 1)
                data[k] = v.strip().strip('"')
    return data

def ensure_docker_installed():
    # Idempotent: if docker exists, skip install (but still ensure compose)
    if which("docker"):
        log("Docker binary already installed.")
    else:
        osinfo = detect_os()
        distro = (osinfo.get("ID") or "").lower()
        name = (osinfo.get("NAME") or "").lower()
        version = (osinfo.get("VERSION_ID") or "")
        arch = osinfo.get("ARCH")
        log(f"Detected OS: ID={distro} NAME={name} VERSION={version} ARCH={arch}")

        try:
            if "amazon" in distro and str(version).startswith("2023"):
                # Amazon Linux 2023
                run("sudo dnf -y update")
                run("sudo dnf -y install docker")
                run("sudo systemctl enable --now docker")
            elif "amzn" in distro or "amazon" in name:
                # Amazon Linux 2 (amazon-linux-extras may exist)
                try:
                    run("sudo amazon-linux-extras enable docker || true", check=False)
                except Exception:
                    log("amazon-linux-extras not available")
                run("sudo yum -y update")
                run("sudo yum -y install -y docker || true")
                run("sudo systemctl enable --now docker")
            elif "ubuntu" in distro or "debian" in distro:
                run("sudo apt-get update -y")
                run("sudo apt-get install -y ca-certificates curl gnupg lsb-release")
                run("sudo mkdir -p /etc/apt/keyrings")
                run("curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg")
                run('echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null')
                run("sudo apt-get update -y")
                run("sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin")
                run("sudo systemctl enable --now docker")
            else:
                # Best-effort fallback: try common package managers
                if which("dnf"):
                    run("sudo dnf -y update")
                    run("sudo dnf -y install docker || true")
                    run("sudo systemctl enable --now docker")
                elif which("yum"):
                    run("sudo yum -y update")
                    run("sudo yum -y install -y docker || true")
                    run("sudo systemctl enable --now docker")
                elif which("apt-get"):
                    run("sudo apt-get update -y")
                    run("sudo apt-get install -y docker.io || true")
                    run("sudo systemctl enable --now docker")
                else:
                    raise RuntimeError("No supported package manager found to install Docker; please install Docker manually.")
        except Exception as e:
            log(f"ERROR installing Docker: {e}")
            raise

    # Allow current user to use docker (ec2-user or detected user)
    try:
        current_user = os.environ.get("USER") or os.environ.get("LOGNAME") or os.getlogin()
    except Exception:
        current_user = "ec2-user"
    log(f"Adding user '{current_user}' to docker group (sudo usermod -aG docker {current_user})")
    try:
        run(f"sudo usermod -aG docker {current_user}", check=False)
    except Exception:
        log("usermod may have failed or already set; continuing")

    # Install docker compose if needed
    compose_ok = False
    try:
        res = subprocess.run("docker compose version", shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if res.returncode == 0:
            compose_ok = True
            log("Docker Compose v2 plugin available.")
    except Exception:
        pass

    if not compose_ok and which("docker-compose"):
        compose_ok = True
        log("docker-compose binary present.")

    if not compose_ok:
        # try distro packages (best-effort)
        if which("dnf"):
            run("sudo dnf -y install docker-compose-plugin || true", check=False)
        if which("yum"):
            run("sudo yum -y install docker-compose-plugin || true", check=False)
        if which("apt-get"):
            run("sudo apt-get install -y docker-compose-plugin || true", check=False)

    if not compose_ok:
        arch = os.uname().machine
        if arch == "aarch64":
            url = "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64"
        else:
            url = "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64"
        run(f"sudo curl -L {url} -o /usr/local/bin/docker-compose")
        run("sudo chmod +x /usr/local/bin/docker-compose")
        log("Installed standalone docker-compose binary to /usr/local/bin/docker-compose")

    # small wait to let docker come up
    time.sleep(1)
    try:
        run("docker --version", check=False)
    except Exception:
        log("Warning: docker --version failed; docker may need restart or login session refresh")

def randpass(n: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "-_!@"
    return ''.join(secrets.choice(alphabet) for _ in range(n))

# Minimal docker-compose templates (small and resource-friendly)
COMPOSE_TEMPLATES = {
    "static": """version: "3.8"
services:
  nginx:
    image: nginx:stable-alpine
    restart: unless-stopped
    volumes:
      - ./www:/usr/share/nginx/html:ro
    ports:
      - "80:80"
""",
    "php": """version: "3.8"
services:
  nginx:
    image: nginx:stable-alpine
    restart: unless-stopped
    volumes:
      - ./www:/var/www/html:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
    ports:
      - "80:80"
    depends_on:
      - php

  php:
    build: ./php
    restart: unless-stopped
    volumes:
      - ./www:/var/www/html
    depends_on:
      - db

  db:
    image: mariadb:10.5
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: "${MYSQL_ROOT_PASSWORD}"
      MYSQL_DATABASE: appdb
      MYSQL_USER: appuser
      MYSQL_PASSWORD: "${MYSQL_PASSWORD}"
    volumes:
      - db_data:/var/lib/mysql

  phpmyadmin:
    image: phpmyadmin/phpmyadmin
    environment:
      PMA_HOST: db
      PMA_USER: root
      PMA_PASSWORD: "${MYSQL_ROOT_PASSWORD}"
    ports:
      - "8080:80"

volumes:
  db_data:
""",
    "node": """version: "3.8"
services:
  app:
    image: node:18-alpine
    working_dir: /usr/src/app
    volumes:
      - ./www:/usr/src/app
    command: sh -c "npm install || true && npm start"
    ports:
      - "3000:3000"
""",
    "django": """version: "3.8"
services:
  web:
    image: python:3.11-alpine
    working_dir: /app
    volumes:
      - ./www:/app
    command: sh -c "pip install -r requirements.txt || true && gunicorn project.wsgi:application --bind 0.0.0.0:8000"
    ports:
      - "8000:8000"

  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: appuser
      POSTGRES_PASSWORD: "${POSTGRES_PASSWORD}"
      POSTGRES_DB: appdb
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
""",
    # Mail server template is intentionally minimal; recommend SES for production
    "mail": """version: "3.8"
services:
  mailserver:
    image: docker.io/mailserver/docker-mailserver:latest
    hostname: mail
    domainname: example.com
    env_file: .env
    ports:
      - "25:25"
      - "587:587"
      - "993:993"
    volumes:
      - maildata:/var/mail
      - mailstate:/var/mail-state
      - ./config/:/tmp/docker-mailserver/
volumes:
  maildata:
  mailstate:
"""
}

def write_compose(profile: str):
    tpl = COMPOSE_TEMPLATES.get(profile)
    if not tpl:
        raise RuntimeError(f"No compose template for profile {profile}")
    COMPOSE_FILE.write_text(tpl)
    log(f"Wrote {COMPOSE_FILE.name} for profile {profile}")

def ensure_dirs(profile: str):
    WWW_DIR.mkdir(exist_ok=True)
    if profile == "php":
        PHP_DIR.mkdir(exist_ok=True)
        NGINX_CONF_DIR.mkdir(parents=True, exist_ok=True)

def place_index():
    src = ROOT / "index.php"
    dst = WWW_DIR / "index.php"
    if src.exists():
        dst.write_text(src.read_text())
        os.chmod(dst, 0o644)
        log("Placed index.php into www/")
    else:
        log("No index.php in repo root to place into www/")

def create_php_files():
    # minimal PHP Dockerfile and nginx conf tailored for low-memory hosts
    dockerfile = """FROM php:8.1-fpm-alpine
WORKDIR /var/www/html
COPY ./www /var/www/html
"""
    (PHP_DIR / "Dockerfile").write_text(dockerfile)
    nginx_conf = """server {
    listen 80;
    server_name _;
    root /var/www/html;
    index index.php index.html;
    location / { try_files $uri $uri/ /index.php?$query_string; }
    location ~ \\.php$ { fastcgi_pass php:9000; include fastcgi_params; fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name; }
}"""
    (NGINX_CONF_DIR / "default.conf").write_text(nginx_conf)
    log("Wrote PHP Dockerfile and nginx default.conf")

def write_env_file(env: Dict[str,str]):
    lines = [f"{k}={v}" for k,v in env.items()]
    ENVFILE.write_text("\n".join(lines) + "\n")
    os.chmod(ENVFILE, 0o600)
    log(f"Wrote {ENVFILE.name} (600)")

def compose_up():
    # prefer docker compose v2
    try:
        run("docker compose pull || true", check=False)
        run("docker compose up -d", check=True)
    except Exception:
        # fallback to docker-compose binary
        run("docker-compose pull || true", check=False)
        run("docker-compose up -d", check=True)

def public_ip() -> str:
    try:
        # quick external IP fetch; non-blocking
        out = subprocess.run("curl -sS https://ifconfig.co/ip", shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5)
        ip = out.stdout.strip()
        if ip:
            return ip
    except Exception:
        pass
    return "UNKNOWN"

def write_readme(info: Dict):
    parts = []
    parts.append("=== QUICKAWS PROVISION SUMMARY ===")
    parts.append(f"Time: {time.asctime()}")
    parts.append(f"Public IP: {info.get('public_ip','UNKNOWN')}")
    parts.append(f"Hostname: {info.get('hostname','UNKNOWN')}")
    parts.append("")
    parts.append("Generated credentials (store securely):")
    parts.append(json.dumps(info.get("secrets",{}), indent=2))
    parts.append("")
    parts.append("Ports:")
    for p in info.get("ports", []):
        parts.append(" - " + p)
    parts.append("")
    parts.append("Notes: README_SECURE.txt is chmod 600. Download via SSH (scp) only.")
    README_SECURE.write_text("\n".join(parts) + "\n")
    os.chmod(README_SECURE, 0o600)
    log(f"Wrote {README_SECURE.name} (600)")

def interactive_choice() -> str:
    if os.environ.get("NONINTERACTIVE") == "1":
        profile = os.environ.get("PROFILE", "php")
        log(f"Running non-interactive with PROFILE={profile}")
        return profile
    # interactive
    print("Choose server purpose:")
    print(" 1. Static Web Server or Serverless")
    print(" 2. Proper Web Server (MariaDB, phpMyAdmin, email opt.)")
    print(" 3. NodeJS server")
    print(" 4. Django based server")
    print(" 5. Mailserver (advanced)")
    choice = input("Select number (e.g. 2): ").strip()
    map_ = {"1":"static","2":"php","3":"node","4":"django","5":"mail"}
    return map_.get(choice, "php")

def main():
    log("=== QuickAWS provisioner starting ===")
    # Choose profile
    profile = interactive_choice()
    log(f"Selected profile: {profile}")

    # install docker if needed
    try:
        ensure_docker_installed()
    except Exception as e:
        log(f"Error installing docker: {e}")
        log("Provisioner cannot continue without Docker. Exiting.")
        sys.exit(1)

    # create directories and copy index
    ensure_dirs(profile)
    place_index()

    # create .env with credentials
    secrets_map = {}
    if profile == "php":
        secrets_map["MYSQL_ROOT_PASSWORD"] = randpass(20)
        secrets_map["MYSQL_PASSWORD"] = randpass(16)
        secrets_map["MYSQL_USER"] = "appuser"
    elif profile == "django":
        secrets_map["POSTGRES_PASSWORD"] = randpass(18)
    elif profile == "mail":
        secrets_map["MAIL_ADMIN_PASS"] = randpass(20)
    # write .env if any
    if secrets_map:
        write_env_file(secrets_map)

    # create compose file
    write_compose(profile)

    # profile-specific files
    if profile == "php":
        create_php_files()

    # bring up containers
    try:
        log("Bringing up docker-compose stack (may take a few minutes on first run)...")
        compose_up()
    except Exception as e:
        log(f"docker compose up failed: {e}")

    # produce README_SECURE
    info = {
        "public_ip": public_ip(),
        "hostname": os.uname().nodename,
        "secrets": secrets_map,
        "ports": ["80 -> nginx", "8080 -> phpmyadmin (if present)"],
    }
    write_readme(info)

    log("Provisioning complete. Secure summary in README_SECURE.txt (600).")
    print("\nProvision complete.")
    print("Secure README:", README_SECURE)
    print("Log file:", LOGFILE)

if __name__ == "__main__":
    main()
