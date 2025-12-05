#!/usr/bin/env python3
"""
QuickAWS provisioner (fixed full version)

- Generates PHP (nginx+php-fpm) docker-compose stack with MariaDB and DB UI (Adminer on ARM, phpMyAdmin on x86_64).
- Binds DB UI to localhost by default for safety.
- Installs Docker and Compose fallback when missing.
- Writes README_SECURE.txt with generated credentials (chmod 600).
- Robust, idempotent, suitable for NONINTERACTIVE=1 runs.
"""

import os
import sys
import subprocess
import time
import random
import string
from pathlib import Path
import shutil
import traceback

LOGFILE = Path("provision.log")

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    out = f"{ts} {msg}"
    print(out)
    try:
        with LOGFILE.open("a") as f:
            f.write(out + "\n")
    except Exception:
        pass

def run(cmd, check=True, env=None):
    """Run shell command, capture output, log and optionally fail."""
    log(f"> {cmd}")
    res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    if res.stdout:
        for line in res.stdout.splitlines():
            log("  " + line)
    if check and res.returncode != 0:
        raise RuntimeError(f"Command failed ({res.returncode}): {cmd}\nOutput:\n{res.stdout}")
    return res

def which(cmd):
    from shutil import which as _which
    return _which(cmd)

def safe_mkdir(p: str, mode=0o755):
    Path(p).mkdir(parents=True, exist_ok=True)
    try:
        Path(p).chmod(mode)
    except Exception:
        pass

def random_pw(n=20):
    alphabet = string.ascii_letters + string.digits + "-_!@"
    return "".join(random.choice(alphabet) for _ in range(n))

def detect_os_arch():
    os_release = {}
    if Path("/etc/os-release").exists():
        for ln in open("/etc/os-release"):
            if "=" in ln:
                k,v = ln.strip().split("=",1)
                os_release[k] = v.strip().strip('"')
    distro = os_release.get("ID","").lower()
    distro_like = os_release.get("ID_LIKE","").lower()
    version = os_release.get("VERSION_ID","")
    arch = os.uname().machine
    return {"distro":distro, "like":distro_like, "version":version, "arch":arch, "os_release":os_release}

def ensure_docker_installed():
    """
    Install Docker if missing. Idempotent. Supports Amazon Linux 2/2023, Debian/Ubuntu, fallback to yum/apt/dnf heuristics.
    Also installs a Docker Compose fallback binary if needed.
    """
    log("Checking Docker/Compose presence...")
    if which("docker"):
        try:
            run("docker --version", check=False)
            log("Docker binary present.")
        except Exception:
            pass
    else:
        info = detect_os_arch()
        distro = info["distro"]
        version = info["version"]
        log(f"Detected distro:{distro} ver:{version} arch:{info['arch']}")
        try:
            # Amazon Linux 2023
            if "amazon" in distro and version.startswith("2023"):
                run("sudo dnf -y update")
                run("sudo dnf -y install docker")
                run("sudo systemctl enable --now docker")
            # Amazon Linux 2 path
            elif "amzn" in distro or "amazon" in distro:
                # try amazon-linux-extras then yum
                try:
                    run("sudo amazon-linux-extras enable docker || true")
                except Exception:
                    log("amazon-linux-extras not available or failed; continuing.")
                run("sudo yum -y update")
                run("sudo yum -y install -y docker || true")
                run("sudo systemctl enable --now docker")
            # Debian/Ubuntu family
            elif "ubuntu" in distro or "debian" in distro or "raspbian" in distro or "pop" in distro:
                run("sudo apt-get update -y")
                run("sudo apt-get install -y ca-certificates curl gnupg lsb-release")
                run("sudo mkdir -p /etc/apt/keyrings")
                run("curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg")
                run('echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null')
                run("sudo apt-get update -y")
                run("sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin")
                run("sudo systemctl enable --now docker")
            else:
                # generic package-manager fallbacks
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
                    raise RuntimeError("Unable to detect package manager to install Docker. Install Docker manually.")
        except Exception as e:
            log("Docker installation encountered error: " + str(e))
            raise

    # Add user to docker group
    try:
        current_user = os.environ.get("USER") or os.environ.get("LOGNAME") or os.getlogin()
    except Exception:
        current_user = "ec2-user"
    log(f"Adding user {current_user} to docker group (if not already member)")
    run(f"sudo usermod -aG docker {current_user} || true", check=False)

    # Ensure containerd/docker running
    try:
        run("sudo systemctl enable --now docker", check=False)
    except Exception:
        pass

    # Install docker-compose fallback binary if no 'docker compose' and no docker-compose
    compose_ok = False
    try:
        res = subprocess.run("docker compose version", shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if res.returncode == 0:
            compose_ok = True
            log("docker compose plugin available.")
    except Exception:
        pass

    if not compose_ok and which("docker-compose"):
        compose_ok = True
        log("docker-compose binary present.")

    if not compose_ok:
        arch = os.uname().machine
        if arch == "aarch64":
            binurl = "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64"
        else:
            binurl = "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64"
        log(f"Downloading docker-compose binary from {binurl}")
        run(f"sudo curl -L {binurl} -o /usr/local/bin/docker-compose")
        run("sudo chmod +x /usr/local/bin/docker-compose")
        log("Installed docker-compose standalone binary to /usr/local/bin/docker-compose")

    # show versions
    run("docker --version", check=False)
    run("docker-compose --version || docker compose version || true", check=False)
    time.sleep(1)

def write_file(path, content, mode=0o644):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    try:
        p.chmod(mode)
    except Exception:
        pass
    log(f"Wrote {path} ({len(content)} bytes)")

def generate_php_compose(arch: str) -> str:
    """
    Generate docker-compose YAML for PHP profile.
    Adminer for ARM (bound to localhost), phpMyAdmin for x86_64.
    The returned content preserves ${VAR} placeholders for environment substitution.
    """
    if arch and ("aarch64" in arch or arch == "arm" or arch.startswith("arm")):
        db_ui_block = """  adminer:
    image: adminer:latest
    restart: unless-stopped
    environment:
      ADMINER_DEFAULT_SERVER: db
    ports:
      - "127.0.0.1:8080:8080"
"""
    else:
        db_ui_block = """  phpmyadmin:
    image: phpmyadmin/phpmyadmin:latest
    restart: unless-stopped
    environment:
      PMA_HOST: db
      PMA_USER: root
      PMA_PASSWORD: "${MYSQL_ROOT_PASSWORD}"
    ports:
      - "127.0.0.1:8080:8080"
"""

    compose = (
"""services:
  nginx:
    image: nginx:stable-alpine
    restart: unless-stopped
    volumes:
      - ./www:/var/www/html:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - /etc/letsencrypt:/etc/letsencrypt:ro
      - /etc/nginx/ssl:/etc/nginx/ssl:ro
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

"""
    + db_ui_block +
"""
volumes:
  db_data:
"""
    )
    return compose

def write_readme_secure(env_path=".env", out_path="README_SECURE.txt"):
    try:
        env = {}
        p = Path(env_path)
        if p.exists():
            for line in p.read_text().splitlines():
                if '=' in line and not line.strip().startswith('#'):
                    k,v = line.split('=',1)
                    env[k.strip()] = v.strip()
        pubip = "UNKNOWN"
        try:
            pubip = subprocess.check_output(["curl","-sS","http://169.254.169.254/latest/meta-data/public-ipv4"], text=True, timeout=2).strip()
        except Exception:
            pass
        content = f"""=== QUICKAWS PROVISION SUMMARY ===
Time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}
Public IP: {pubip}
Hostname: {os.uname().nodename}

Generated credentials (store securely):
{{
  "MYSQL_ROOT_PASSWORD": "{env.get('MYSQL_ROOT_PASSWORD','')}",
  "MYSQL_USER": "{env.get('MYSQL_USER','appuser')}",
  "MYSQL_PASSWORD": "{env.get('MYSQL_PASSWORD','')}"
}}

Ports:
 - 80 -> nginx
 - 8080 -> DB UI (bound to localhost by default)

Notes:
 - README_SECURE.txt is chmod 600. Download via SSH (scp) only.
"""
        p_out = Path(out_path)
        p_out.write_text(content)
        try:
            p_out.chmod(0o600)
        except Exception:
            pass
        log(f"Wrote {out_path} (600)")
    except Exception as e:
        log("Failed to write README_SECURE.txt: " + str(e))

def safe_write_dotenv(rootpw=None, userpw=None, user="appuser"):
    if not rootpw:
        rootpw = random_pw()
    if not userpw:
        userpw = random_pw()
    env_path = Path(".env")
    content_lines = [
        f"MYSQL_ROOT_PASSWORD={rootpw}",
        f"MYSQL_DATABASE=appdb",
        f"MYSQL_USER={user}",
        f"MYSQL_PASSWORD={userpw}"
    ]
    env_path.write_text("\n".join(content_lines) + "\n")
    try:
        env_path.chmod(0o600)
    except Exception:
        pass
    log(".env written with generated credentials")
    return rootpw, userpw, user

def write_index_php(src_index=None):
    # If repo has index.php (in current dir), use it; otherwise write a default
    www = Path("www")
    www.mkdir(parents=True, exist_ok=True)
    target = www / "index.php"
    if Path("index.php").exists():
        try:
            shutil.copy("index.php", target)
            log("Copied index.php into www/")
        except Exception:
            # fallback: write a simple index
            target.write_text("<?php phpinfo(); ?>")
            log("Wrote fallback index.php")
    else:
        content = """<?php
echo \"<h2>QuickAWS: PHP stack is running</h2>\\n\";
echo \"<p>Server time: \" . date('c') . \"</p>\\n\";
?>"""
        target.write_text(content)
        log("Wrote default www/index.php")

def write_nginx_conf():
    conf_dir = Path("nginx/conf.d")
    conf_dir.mkdir(parents=True, exist_ok=True)
    conf = r'''
server {
    listen 80;
    server_name _;

    root /var/www/html;
    index index.php index.html index.htm;

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }

    location ~ \.php$ {
        fastcgi_pass php:9000;
        fastcgi_index index.php;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    }

    location /adminer/ {
        # proxy (if using nginx proxy approach)
        proxy_pass http://127.0.0.1:8080/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
'''
    write_file(conf_dir / "default.conf", conf, mode=0o644)
    log("Wrote nginx/conf.d/default.conf")

def try_docker_compose_up(compose_path="docker-compose.yml"):
    """
    Try docker compose using several fallbacks:
     - docker compose up -d
     - docker-compose up -d
     - newgrp docker -c "docker-compose up -d"
     - sudo docker-compose up -d --remove-orphans --build
    Returns True on success, False otherwise.
    """
    cmds = [
        "docker compose up -d",
        "docker-compose up -d",
        'newgrp docker -c "docker-compose up -d"',
        "sudo docker-compose up -d --remove-orphans --build"
    ]
    last_out = ""
    for c in cmds:
        try:
            log(f"Attempting compose with: {c}")
            r = run(c, check=False)
            if r.returncode == 0:
                log("Compose started successfully.")
                return True
            else:
                last_out = getattr(r, "stdout", "") or ""
        except Exception as e:
            last_out = str(e)
    log("All compose attempts failed. Last output:\n" + last_out)
    return False

def validate_compose_yaml():
    try:
        run("sudo docker-compose config", check=False)
    except Exception as e:
        log("docker-compose config failed (nonfatal here): " + str(e))

def main():
    try:
        log("=== QuickAWS provisioner starting ===")
        info = detect_os_arch()
        arch = info["arch"]
        log(f"Detected arch: {arch} distro: {info['distro']}")

        # ensure docker present
        ensure_docker_installed()

        # prepare working dir
        safe_mkdir("www")
        safe_mkdir("nginx/conf.d")

        # create .env (if absent) and keep credentials
        if not Path(".env").exists():
            rootpw, userpw, user = safe_write_dotenv()
        else:
            # read existing
            data = {}
            for ln in Path(".env").read_text().splitlines():
                if '=' in ln:
                    k,v = ln.split('=',1)
                    data[k.strip()] = v.strip()
            rootpw = data.get("MYSQL_ROOT_PASSWORD") or random_pw()
            userpw = data.get("MYSQL_PASSWORD") or random_pw()
            user = data.get("MYSQL_USER") or "appuser"
            # re-write to ensure permissions
            safe_write_dotenv(rootpw=rootpw, userpw=userpw, user=user)

        # write index/nginx
        write_index_php()
        write_nginx_conf()

        # generate compose
        compose_text = generate_php_compose(arch)
        write_file("docker-compose.yml", compose_text)

        # show compose validation
        validate_compose_yaml()

        # Attempt to bring up stack
        log("Bringing up docker-compose stack (may take a few minutes on first run)...")
        ok = try_docker_compose_up()
        if not ok:
            log("Compose failed. Try running with sudo or checking docker permissions.")
        else:
            log("Compose up attempted (check containers with sudo docker ps -a).")

        # After running compose (or even if not), write README_SECURE
        write_readme_secure()

        log("Provisioning complete.")
        log("Secure README: " + str(Path("README_SECURE.txt").absolute()))
        log("Log file: " + str(LOGFILE.absolute()))
    except Exception as exc:
        log("Provisioner failed with exception:")
        log(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    # allow non-interactive override env/profile in future (kept for compatibility)
    main()
