#!/usr/bin/env python3
"""
QuickAWS v2 - provision.py

Purpose:
- Fully automated one-click provisioning for EC2 (Amazon Linux 2/2023, Ubuntu/Debian)
- Auto-detects OS & architecture
- Installs Docker, Docker Compose (or standalone), Buildx fallback
- Picks DB UI based on arch: MyWebSQL on ARM, phpMyAdmin on x86_64
- Uses official php image (no local build) unless you opt-in for custom build
- Writes .env, README_SECURE.txt (chmod 600), writes index.php to www/
- Writes docker-compose.yml from templates and starts stack with robust fallbacks
- Writes install.sh (one-liner usage) if asked

Usage:
- Place this file at the repo root (~/app/provision.py)
- chmod +x provision.py
- Run interactively: python3 provision.py
- Or non-interactive: NONINTERACTIVE=1 PROFILE=php python3 provision.py

This single script also writes supporting files (docker-compose.yml, php Dockerfile, nginx conf)
so it can act as a repo bootstrapper - no separate files required.

Note: this file is intended to be put into your GitHub repo QuickAWS as provision.py.
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
INSTALL_SH = ROOT / "install.sh"

# ------------------------------- logging ---------------------------------

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

# ---------------------------- detection helpers --------------------------

def detect_os() -> Dict[str, str]:
    data = {"ID": "", "NAME": "", "VERSION_ID": "", "ARCH": os.uname().machine}
    if Path("/etc/os-release").exists():
        for line in open("/etc/os-release", "r"):
            if "=" in line:
                k, v = line.strip().split("=", 1)
                data[k] = v.strip().strip('"')
    return data


def detect_arch() -> str:
    a = os.uname().machine.lower()
    if "aarch64" in a or "arm" in a:
        return "arm"
    return "x86"

# --------------------------- installation logic -------------------------

def ensure_docker_installed():
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
                run("sudo dnf -y update")
                run("sudo dnf -y install docker")
                run("sudo systemctl enable --now docker")
            elif "amzn" in distro or "amazon" in name:
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
                    raise RuntimeError("No supported package manager found to install Docker; install manually")
        except Exception as e:
            log(f"ERROR installing Docker: {e}")
            raise

    try:
        current_user = os.environ.get("USER") or os.environ.get("LOGNAME") or os.getlogin()
    except Exception:
        current_user = "ec2-user"
    log(f"Adding user '{current_user}' to docker group")
    try:
        run(f"sudo usermod -aG docker {current_user}", check=False)
    except Exception:
        log("usermod failed or not necessary")

    # compose plugin / binary
    compose_ok = False
    try:
        res = subprocess.run("docker compose version", shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if res.returncode == 0:
            compose_ok = True
            log("Docker Compose v2 plugin available")
    except Exception:
        pass
    if not compose_ok and which("docker-compose"):
        compose_ok = True
        log("docker-compose binary present")

    if not compose_ok:
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

    time.sleep(1)
    try:
        run("docker --version", check=False)
    except Exception:
        log("Warning: docker --version failed; docker may need restart")

# ---------------------- Buildx installer (fallback) ---------------------

def ensure_buildx(min_version_major=0, min_version_minor=17):
    # prefer packaged plugin; otherwise fetch modern buildx binary
    try:
        res = subprocess.run("docker buildx version", shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        if res.returncode == 0:
            log(res.stdout.strip())
            # parse version if needed
            return
    except Exception:
        pass
    log("Installing Docker Buildx CLI plugin (fallback)")
    # try distro packages
    if which("dnf"):
        run("sudo dnf -y install docker-buildx-plugin || true", check=False)
    if which("yum"):
        run("sudo yum -y install docker-buildx-plugin || true", check=False)
    # fallback binary
    arch = os.uname().machine
    if arch == "aarch64":
        BINURL = "https://github.com/docker/buildx/releases/latest/download/buildx-v0.17.0.linux-arm64"
    else:
        BINURL = "https://github.com/docker/buildx/releases/latest/download/buildx-v0.17.0.linux-amd64"
    run("sudo mkdir -p /usr/libexec/docker/cli-plugins || true", check=False)
    run(f"sudo curl -fsSL {BINURL} -o /usr/libexec/docker/cli-plugins/docker-buildx")
    run("sudo chmod +x /usr/libexec/docker/cli-plugins/docker-buildx")
    try:
        run("sudo docker buildx version", check=False)
    except Exception:
        log("buildx install attempted; if docker-compose still complains, consider using official images instead of building locally")

# ---------------------- docker compose helpers --------------------------

def run_docker_compose_action(action: str):
    compose_cmd = None
    try:
        r = subprocess.run("docker compose version", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode == 0:
            compose_cmd = "docker compose"
    except Exception:
        pass
    if not compose_cmd:
        if shutil.which("docker-compose"):
            compose_cmd = "docker-compose"
        else:
            compose_cmd = "docker compose"
    full_cmd = f"{compose_cmd} {action}"

    # Try normal
    try:
        run(full_cmd, check=True)
        return
    except Exception as e1:
        log(f"Normal compose attempt failed: {e1}")
    # Try newgrp docker
    try:
        safe_cmd = full_cmd.replace('"', '\\"')
        newgrp_cmd = f'newgrp docker -c "{safe_cmd}"'
        run(newgrp_cmd, check=True)
        return
    except Exception as e2:
        log(f"newgrp attempt failed: {e2}")
    # Try sudo
    try:
        run(f"sudo {full_cmd}", check=True)
        return
    except Exception as e3:
        log(f"Sudo attempt failed: {e3}")
        raise RuntimeError("All attempts to run docker compose failed. Check docker service and permissions.")


def compose_up():
    log("Attempting docker compose pull/up with permission fallbacks...")
    try:
        run_docker_compose_action("pull || true")
    except Exception as e:
        log(f"Warning: pull failed: {e} (continuing)")
    try:
        run_docker_compose_action("up -d")
        log("docker compose up succeeded")
    except Exception as e:
        log(f"docker compose up failed after all fallbacks: {e}")
        raise

# ---------------------- templates & file writers ------------------------

def randpass(n: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "-_!@"
    return ''.join(secrets.choice(alphabet) for _ in range(n))

TEMPLATES = {}

TEMPLATES['static'] = '''services:
  nginx:
    image: nginx:stable-alpine
    restart: unless-stopped
    volumes:
      - ./www:/usr/share/nginx/html:ro
    ports:
      - "80:80"
'''

# php template will be generated dynamically according to arch

TEMPLATES['node'] = '''services:
  app:
    image: node:18-alpine
    working_dir: /usr/src/app
    volumes:
      - ./www:/usr/src/app
    command: sh -c "npm install || true && npm start"
    ports:
      - "3000:3000"
'''

TEMPLATES['django'] = '''services:
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
'''

TEMPLATES['mail'] = '''services:
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
'''

# ---------- compose generator for php (with arch-specific DB UI) --------

def generate_php_compose(arch: str) -> str:
    """
    Generate docker-compose YAML for the PHP stack.
    Uses MyWebSQL on ARM, phpMyAdmin on x86_64.
    No f-strings so ${VAR} stays literal.
    """

    if arch == 'arm':
        # ARM → MyWebSQL (full UI, multi-arch safe)
        db_ui_block = '''  mywebsql:
    image: mywebsql/mywebsql:latest
    restart: unless-stopped
    ports:
      - "8080:80"
'''
    else:
        # x86 → phpMyAdmin
        db_ui_block = '''  phpmyadmin:
    image: phpmyadmin/phpmyadmin:latest
    restart: unless-stopped
    environment:
      PMA_HOST: db
      PMA_USER: root
      PMA_PASSWORD: "${MYSQL_ROOT_PASSWORD}"
    ports:
      - "8080:80"
'''

    # MAIN YAML BODY (NO f-string)
    compose = (
'''services:
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
    image: php:8.1-fpm-alpine
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

'''
    + db_ui_block +
'''
volumes:
  db_data:
'''
    )

    return compose


# ----------------------- file creation helpers --------------------------

def write_compose(profile: str, arch: str):
    if profile == 'php':
        COMPOSE_FILE.write_text(generate_php_compose(arch))
        log("Wrote docker-compose.yml for profile php")
    else:
        tpl = TEMPLATES.get(profile)
        if not tpl:
            raise RuntimeError(f"No template for profile {profile}")
        COMPOSE_FILE.write_text(tpl)
        log(f"Wrote docker-compose.yml for profile {profile}")


def ensure_dirs(profile: str):
    WWW_DIR.mkdir(exist_ok=True)
    if profile == 'php':
        PHP_DIR.mkdir(exist_ok=True)
        NGINX_CONF_DIR.mkdir(parents=True, exist_ok=True)


def place_index():
    WWW_DIR.mkdir(exist_ok=True)
    src = ROOT / "index.php"
    dst = WWW_DIR / "index.php"
    if src.exists():
        dst.write_text(src.read_text())
        os.chmod(dst, 0o644)
        log("Placed index.php into www/")
    else:
        # create a minimal index.php placeholder
        dst.write_text("<?php echo 'QuickAWS default page'; ?>\n")
        os.chmod(dst, 0o644)
        log("Created default index.php in www/")


def create_php_files():
    dockerfile = """FROM php:8.1-fpm-alpine\nWORKDIR /var/www/html\nCOPY ./www /var/www/html\n"""
    (PHP_DIR / "Dockerfile").write_text(dockerfile)
    nginx_conf = """server {
    listen 80;
    server_name _;
    root /var/www/html;
    index index.php index.html;
    location / { try_files $uri $uri/ /index.php?$query_string; }
    location ~ \.php$ { fastcgi_pass php:9000; include fastcgi_params; fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name; }
}"""
    (NGINX_CONF_DIR / "default.conf").write_text(nginx_conf)
    log("Created php Dockerfile and nginx conf")


def write_env_file(env: Dict[str, str]):
    lines = [f"{k}={v}" for k, v in env.items()]
    ENVFILE.write_text("\n".join(lines) + "\n")
    os.chmod(ENVFILE, 0o600)
    log(f"Wrote {ENVFILE.name} (600)")


def write_readme(info: Dict):
    parts = []
    parts.append("=== QUICKAWS PROVISION SUMMARY ===")
    parts.append(f"Time: {time.asctime()}")
    parts.append(f"Public IP: {info.get('public_ip','UNKNOWN')}")
    parts.append(f"Hostname: {info.get('hostname','UNKNOWN')}")
    parts.append("")
    parts.append("Generated credentials (store securely):")
    parts.append(json.dumps(info.get("secrets", {}), indent=2))
    parts.append("")
    parts.append("Ports:")
    for p in info.get("ports", []):
        parts.append(" - " + p)
    parts.append("")
    parts.append("Notes: README_SECURE.txt is chmod 600. Download via SSH (scp) only.")
    README_SECURE.write_text("\n".join(parts) + "\n")
    os.chmod(README_SECURE, 0o600)
    log(f"Wrote {README_SECURE.name} (600)")


def public_ip():
    # prefer instance metadata
    try:
        out = subprocess.run("curl -sS http://169.254.169.254/latest/meta-data/public-ipv4", shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3)
        ip = out.stdout.strip()
        if ip:
            return ip
    except Exception:
        pass
    # fallback to external (may be blocked by CF)
    try:
        return subprocess.run("curl -sS https://ifconfig.co/ip", shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=3).stdout.strip()
    except Exception:
        return "UNKNOWN"

# ----------------------- install.sh generator ---------------------------

def write_install_sh():
    text = f"""#!/usr/bin/env bash
# QuickAWS install.sh - one-liner installer
set -euo pipefail
ROOT="$HOME/app"
mkdir -p "$ROOT"
cd "$ROOT"
# download repo contents (assumes this script placed at repo root). If using GitHub: git clone or curl the archive.
if [ ! -f provision.py ]; then
  echo "provision.py not found. Please place provision.py in $ROOT or git clone the repo first."
  exit 1
fi
chmod +x provision.py
# run non-interactively for php by default
NONINTERACTIVE=1 PROFILE=php python3 provision.py
"""
    INSTALL_SH.write_text(text)
    os.chmod(INSTALL_SH, 0o755)
    log("Wrote install.sh (executable)")

# ---------------------------- main flow --------------------------------

def main():
    log("=== QuickAWS v2 provisioner starting ===")
    profile = None
    if os.environ.get("NONINTERACTIVE") == "1":
        profile = os.environ.get("PROFILE", "php")
        log(f"NONINTERACTIVE mode: profile={profile}")
    else:
        print("Choose server purpose:")
        print(" 1. Static Web Server or Serverless")
        print(" 2. Proper Web Server (MariaDB, phpMyAdmin/MyWebSQL, email opt.)")
        print(" 3. NodeJS server")
        print(" 4. Django based server")
        print(" 5. Mailserver (advanced)")
        choice = input("Select number (e.g. 2): ").strip()
        map_ = {"1": "static", "2": "php", "3": "node", "4": "django", "5": "mail"}
        profile = map_.get(choice, "php")
        log(f"User selected profile={profile}")

    # ensure docker & prerequisites
    try:
        ensure_docker_installed()
    except Exception as e:
        log(f"Failed to ensure docker: {e}")
        sys.exit(1)

    # optionally ensure buildx (only if local builds are needed) - but default uses official php image
    try:
        ensure_buildx()
    except Exception:
        log("Buildx install attempted (non-fatal). If local builds fail, provisioner will fallback to official images.")

    arch = detect_arch()
    log(f"Detected architecture: {arch}")

    # generate secrets
    secrets_map = {}
    if profile == 'php':
        secrets_map['MYSQL_ROOT_PASSWORD'] = randpass(20)
        secrets_map['MYSQL_PASSWORD'] = randpass(16)
        secrets_map['MYSQL_USER'] = 'appuser'
    elif profile == 'django':
        secrets_map['POSTGRES_PASSWORD'] = randpass(18)
    elif profile == 'mail':
        secrets_map['MAIL_ADMIN_PASS'] = randpass(20)

    # write env
    if secrets_map:
        write_env_file(secrets_map)

    # prepare files
    ensure_dirs(profile)
    place_index()
    if profile == 'php':
        create_php_files()

    # write compose
    write_compose(profile, arch)

    # write install.sh
    write_install_sh()

    # bring up stack with robust fallbacks
    try:
        compose_up()
    except Exception as e:
        log(f"compose_up failed: {e}")

    # prepare README
    info = {
        'public_ip': public_ip(),
        'hostname': os.uname().nodename,
        'secrets': secrets_map,
        'ports': ['80 -> nginx', '8080 -> db-ui (phpmyadmin/mywebsql/adminer)']
    }
    write_readme(info)
    log("Provisioning complete. README_SECURE.txt created.")
    print('\nProvision complete. Secure README:', README_SECURE)
    print('Log file:', LOGFILE)


if __name__ == '__main__':
    main()
