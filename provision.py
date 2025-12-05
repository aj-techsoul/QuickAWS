#!/usr/bin/env python3
"""
provision.py

Interactive provisioning script. Usage:
  python3 provision.py     # interactive
  NONINTERACTIVE=1 python3 provision.py --profile php  # non-interactive with env vars

It expects to run as a user with docker privileges (ec2-user in Amazon Linux).
"""

import os, sys, stat, subprocess, secrets, string, json, time
from pathlib import Path
from getpass import getpass
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "services"
APP_DIR.mkdir(exist_ok=True)
ENV_FILE = ROOT / ".env"
README = ROOT / "README_SECURE.txt"

DEFAULT_PW_LEN = 20

def public_ip():
    try:
        return urlopen('https://ifconfig.co/ip', timeout=3).read().decode().strip()
    except Exception:
        # fallback to socket
        import socket
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "UNKNOWN"

def generate_password(n=DEFAULT_PW_LEN):
    alphabet = string.ascii_letters + string.digits + "-_!@"
    return ''.join(secrets.choice(alphabet) for _ in range(n))

def write_file(path:Path, content:str, mode=0o644):
    path.write_text(content)
    os.chmod(path, mode)

def run(cmd, check=True):
    print(f"> {cmd}")
    res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(res.stdout)
    if check and res.returncode != 0:
        raise SystemExit(f"Command failed: {cmd}")

# Templates for services
TEMPLATES = {}

TEMPLATES['static'] = """version: "3.8"
services:
  nginx:
    image: nginx:stable-alpine
    restart: unless-stopped
    volumes:
      - ./www:/usr/share/nginx/html:ro
    ports:
      - "80:80"
      - "443:443"
"""

TEMPLATES['php'] = """version: "3.8"
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
    environment:
      - MYSQL_HOST=db
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
"""

TEMPLATES['node'] = """version: "3.8"
services:
  app:
    image: node:18-alpine
    working_dir: /usr/src/app
    volumes:
      - ./www:/usr/src/app
    command: sh -c "npm install || true && npm start"
    ports:
      - "3000:3000"
"""

TEMPLATES['django'] = """version: "3.8"
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
"""

TEMPLATES['mail'] = """version: "3.8"
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

PROFILES = {
    "1": ("Static Web Server or Serverless", "static"),
    "2": ("Proper Web Server (MariaDB, phpMyAdmin, email opt.)", "php"),
    "3": ("NodeJS server", "node"),
    "4": ("Django based server", "django"),
    "5": ("Mailserver (advanced)", "mail"),
}

def prompt_choice():
    print("Choose server purpose:")
    for k,(desc,_) in PROFILES.items():
        print(f" {k}. {desc}")
    choice = input("Select number (e.g. 2): ").strip()
    if choice not in PROFILES:
        print("Invalid choice, defaulting to 1")
        choice = "1"
    return PROFILES[choice][1], PROFILES[choice][0]

def ensure_docker_installed():
    if not shutil_which("docker"):
        print("Docker not installed. Attempting to install (requires sudo).")
        # Try amazon linux / ubuntu detection
        if Path("/etc/os-release").exists():
            osrel = Path("/etc/os-release").read_text()
            if "amzn" in osrel:
                run("sudo amazon-linux-extras enable docker || true")
                run("sudo yum install -y docker")
            else:
                run("sudo apt-get update && sudo apt-get install -y docker.io")
        run("sudo systemctl start docker || true")
        run("sudo usermod -a -G docker $USER || true")
    else:
        print("Docker present")

def shutil_which(name):
    from shutil import which
    return which(name)

def maybe_setup_env_for_profile(profile_key):
    env = {}
    # generate common secrets
    env['MYSQL_ROOT_PASSWORD'] = generate_password(18)
    env['MYSQL_PASSWORD'] = generate_password(16)
    env['MYSQL_USER'] = 'appuser'

    env['POSTGRES_PASSWORD'] = generate_password(16)
    env['APP_SECRET_KEY'] = generate_password(32)
    # If mail profile, also create mail password
    env['MAIL_ADMIN_PASS'] = generate_password(20)
    return env

def write_env_file(envd):
    lines = [f"{k}={v}" for k,v in envd.items()]
    write_file(ENV_FILE, "\n".join(lines)+"\n", mode=0o600)
    print(f"Wrote env file {ENV_FILE} (600)")

def create_compose_for(profile):
    compose_tpl = TEMPLATES.get(profile)
    if not compose_tpl:
        raise SystemExit("No template for profile " + profile)
    target = ROOT / "docker-compose.yml"
    write_file(target, compose_tpl, mode=0o644)
    print(f"Wrote docker-compose.yml for profile {profile}")

def create_readme(info:dict):
    lines = []
    lines.append("=== PROVISIONING SUMMARY ===")
    lines.append(f"Time: {time.asctime()}")
    lines.append(f"Public IP: {info.get('public_ip')}")
    lines.append(f"Host: {info.get('hostname')}")
    lines.append("")
    lines.append("Services and credentials:")
    lines.append(json.dumps(info.get("secrets", {}), indent=2))
    lines.append("")
    lines.append("Ports:")
    for p in info.get("ports", []):
        lines.append(" - " + p)
    lines.append("")
    lines.append("Notes:")
    lines.append(" - This file is stored with permission 600. Access via SSH only.")
    content = "\n".join(lines)
    write_file(README, content, mode=0o600)
    print(f"Wrote README to {README} (600)")

def bring_up_compose():
    # prefer `docker compose` (v2), fallback to docker-compose
    try:
        run("docker compose pull || true", check=False)
        run("docker compose up -d", check=True)
    except Exception:
        run("docker-compose pull || true", check=False)
        run("docker-compose up -d", check=True)

def maybe_upload_to_s3(readme_path):
    if not shutil_which("aws"):
        print("AWS CLI not present — skipping S3 upload")
        return
    ans = input("Upload README_SECURE.txt to S3 (requires IAM role) [y/N]? ").strip().lower()
    if ans != "y":
        return
    bucket = input("Enter S3 bucket name (must exist): ").strip()
    key = f"server-readmes/{os.uname().nodename}-{int(time.time())}.txt"
    run(f"aws s3 cp {readme_path} s3://{bucket}/{key} --sse AES256")
    print(f"Uploaded to s3://{bucket}/{key} (server IAM permissions required)")

def main():
    noninteractive = os.environ.get("NONINTERACTIVE") == "1"
    profile = None
    profile_desc = None
    if noninteractive:
        # allow override via env PROFILE
        pref = os.environ.get("PROFILE", "static")
        profile = pref
        profile_desc = pref
        print(f"Noninteractive: using profile {profile}")
    else:
        profile, profile_desc = prompt_choice()

    # ensure docker exists (best-effort)
    try:
        ensure_docker_installed()
    except Exception as e:
        print("Warning: docker install check failed:", e)

    # generate secrets & write .env
    envd = maybe_setup_env_for_profile(profile)
    write_env_file(envd)

    # write service files
    create_compose_for(profile)

    # create small service-specific directories if needed
    (ROOT / "www").mkdir(exist_ok=True)

    # copy initial index.php if exists in repo root
    uploaded_index = ROOT / "index.php"
    target_index = ROOT / "www/index.php"

    if uploaded_index.exists():
        print("Placing uploaded index.php into web root...")
        target_index.write_text(uploaded_index.read_text())
        os.chmod(target_index, 0o644)
    else:
        print("No index.php found in root to deploy.")

    #----    

    if profile == "php":
        # create a minimal php Dockerfile folder
        php_dir = ROOT / "php"
        php_dir.mkdir(exist_ok=True)
        dockerfile = """FROM php:8.1-fpm-alpine
WORKDIR /var/www/html
COPY ./www /var/www/html
"""
        write_file(php_dir / "Dockerfile", dockerfile, mode=0o644)
        # create a minimal nginx conf if not present
        nginx_dir = ROOT / "nginx"
        conf_dir = nginx_dir / "conf.d"
        conf_dir.mkdir(parents=True, exist_ok=True)
        nginx_conf = """server {
    listen 80;
    server_name _;
    root /var/www/html;
    index index.php index.html;
    location / { try_files $uri $uri/ /index.php?$query_string; }
    location ~ \\.php$ { fastcgi_pass php:9000; include fastcgi_params; fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name; }
}"""
        write_file(conf_dir / "default.conf", nginx_conf, mode=0o644)

    # bring up services
    print("Bringing up docker-compose stack (may take a while the first time)...")
    try:
        bring_up_compose()
    except Exception as e:
        print("docker compose up failed:", e)

    info = {
        "public_ip": public_ip(),
        "hostname": os.uname().nodename,
        "secrets": {
            "ENV_VARS": envd
        },
        "ports": ["80 -> nginx", "443 -> nginx"]
    }
    create_readme(info)

    # final step: restrict the README to owner only; ensure owner is current user
    os.chmod(README, 0o600)
    print("\nPROVISION COMPLETE.")
    print(f"Secure README: {README} (600) — download via SSH only (scp/ssh cat).")
    print("Tip: to view it via SSH: ssh user@host 'sudo cat /path/to/README_SECURE.txt' or scp to your workstation.")
    maybe_upload_to_s3(README)

if __name__ == "__main__":
    main()
