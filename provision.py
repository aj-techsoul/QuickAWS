# from ~/app
cd ~/app

# backup old script
cp provision.py provision.py.bak.$(date +%s)
echo "Backed up old provision.py -> provision.py.bak.*"

# write new robust provision.py
cat > provision.py <<'PY'
#!/usr/bin/env python3
"""
Robust QuickAWS provisioner (verbose). Writes logs to ./provision.log.
Replaces previous provision.py with better auto-detect and main invocation.
"""
import os, sys, time, json, secrets, string, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = ROOT / "provision.log"
ENV_FILE = ROOT / ".env"
README = ROOT / "README_SECURE.txt"

def log(msg):
    line = f"{time.asctime()}  {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\\n")

def run(cmd, check=True):
    log(f"> {cmd}")
    res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    out = res.stdout or ""
    if out.strip():
        for L in out.splitlines():
            log("  " + L)
    if check and res.returncode != 0:
        log(f"Command failed: {cmd} (exit {res.returncode})")
        raise SystemExit(f"Command failed: {cmd}")

def shutil_which(name):
    from shutil import which
    return which(name)

def detect_os():
    os_release = {}
    if Path("/etc/os-release").exists():
        for line in open("/etc/os-release"):
            if "=" in line:
                k,v = line.strip().split("=",1)
                os_release[k] = v.strip().strip('"')
    distro = os_release.get("ID","").lower()
    name = os_release.get("NAME","")
    version = os_release.get("VERSION_ID","")
    arch = os.uname().machine
    return distro, name, version, arch

def ensure_docker_installed():
    """Best-effort docker + compose installer. Idempotent."""
    if shutil_which("docker"):
        log("Docker already present.")
    else:
        distro, name, version, arch = detect_os()
        log(f"Detected: distro={distro!r} name={name!r} version={version!r} arch={arch!r}")
        try:
            if "amazon" in distro and str(version).startswith("2023"):
                run("sudo dnf -y update")
                run("sudo dnf -y install docker")
                run("sudo systemctl enable --now docker")
            elif "amzn" in distro or "amazon" in name.lower() or "amazon" in distro:
                # Amazon Linux 2 / older path
                try:
                    run("sudo amazon-linux-extras enable docker || true")
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
                # fallback
                if shutil_which("dnf"):
                    run("sudo dnf -y update")
                    run("sudo dnf -y install docker || true")
                    run("sudo systemctl enable --now docker")
                elif shutil_which("yum"):
                    run("sudo yum -y update")
                    run("sudo yum -y install -y docker || true")
                    run("sudo systemctl enable --now docker")
                elif shutil_which("apt-get"):
                    run("sudo apt-get update -y")
                    run("sudo apt-get install -y docker.io || true")
                    run("sudo systemctl enable --now docker")
                else:
                    raise SystemExit("No package manager found to install Docker. Install manually.")
        except Exception as e:
            log(f"Docker install error: {e}")
            raise

    # allow ec2-user or current user to run docker
    try:
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or os.getlogin()
    except Exception:
        user = "ec2-user"
    log(f"Adding {user} to docker group")
    run(f"sudo usermod -aG docker {user} || true", check=False)

    # ensure docker compose present
    compose_ok = False
    try:
        res = subprocess.run("docker compose version", shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if res.returncode == 0:
            compose_ok = True
            log("docker compose v2 plugin available")
    except Exception:
        pass
    if not compose_ok and shutil_which("docker-compose"):
        compose_ok = True
        log("docker-compose binary exists")

    if not compose_ok:
        # try to install plugin via package manager
        if shutil_which("dnf"):
            run("sudo dnf -y install docker-compose-plugin || true", check=False)
        if shutil_which("yum"):
            run("sudo yum -y install docker-compose-plugin || true", check=False)
        if shutil_which("apt-get"):
            run("sudo apt-get install -y docker-compose-plugin || true", check=False)

    if not compose_ok:
        arch = os.uname().machine
        if arch == "aarch64":
            binurl = "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64"
        else:
            binurl = "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64"
        run(f"sudo curl -L {binurl} -o /usr/local/bin/docker-compose")
        run("sudo chmod +x /usr/local/bin/docker-compose")

    # quick checks
    run("docker --version", check=False)
    run("docker compose version || docker-compose --version", check=False)
    time.sleep(1)

def generate_password(n=18):
    alphabet = string.ascii_letters + string.digits + "-_!@"
    return ''.join(secrets.choice(alphabet) for _ in range(n))

# templates (small)
TEMPLATES = {
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
"""
}

def create_compose(profile):
    tpl = TEMPLATES.get(profile)
    if not tpl:
        raise SystemExit("Unknown profile: " + profile)
    (ROOT / "docker-compose.yml").write_text(tpl)
    log("Wrote docker-compose.yml")

def write_env(envd):
    lines = [f"{k}={v}" for k,v in envd.items()]
    ENV_FILE.write_text("\\n".join(lines) + "\\n")
    os.chmod(ENV_FILE, 0o600)
    log(f"Wrote {ENV_FILE} (600)")

def place_index():
    (ROOT / "www").mkdir(exist_ok=True)
    src = ROOT / "index.php"
    dst = ROOT / "www" / "index.php"
    if src.exists():
        dst.write_text(src.read_text())
        os.chmod(dst, 0o644)
        log("Placed index.php into www/")
    else:
        log("No index.php in repo root to place")

def create_php_files():
    # minimal php Dockerfile + nginx conf
    phpdir = ROOT / "php"
    nginxconf = ROOT / "nginx" / "conf.d"
    phpdir.mkdir(exist_ok=True)
    nginxconf.mkdir(parents=True, exist_ok=True)
    (phpdir / "Dockerfile").write_text("FROM php:8.1-fpm-alpine\\nWORKDIR /var/www/html\\nCOPY ./www /var/www/html\\n")
    (nginxconf / "default.conf").write_text("""server {
    listen 80;
    server_name _;
    root /var/www/html;
    index index.php index.html;
    location / { try_files $uri $uri/ /index.php?$query_string; }
    location ~ \\.php$ { fastcgi_pass php:9000; include fastcgi_params; fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name; }
}""")
    log("Created php Dockerfile and nginx conf")

def bring_up():
    # prefer docker compose v2
    try:
        run("docker compose pull || true", check=False)
        run("docker compose up -d", check=True)
    except Exception:
        run("docker-compose pull || true", check=False)
        run("docker-compose up -d", check=True)

def write_readme(info):
    parts = []
    parts.append("PROVISION SUMMARY")
    parts.append("Time: " + time.asctime())
    parts.append("Public IP: " + info.get("public_ip","UNKNOWN"))
    parts.append("Hostname: " + info.get("hostname","UNKNOWN"))
    parts.append("")
    parts.append("Credentials:")
    parts.append(json.dumps(info.get("secrets",{}), indent=2))
    parts.append("")
    parts.append("Ports:")
    for p in info.get("ports",[]):
        parts.append(" - " + p)
    parts.append("")
    parts.append("Notes: README_SECURE.txt is chmod 600, download via SSH only.")
    README.write_text("\\n".join(parts))
    os.chmod(README, 0o600)
    log(f"Wrote {README} (600)")

def public_ip():
    try:
        import urllib.request
        return urllib.request.urlopen("https://ifconfig.co/ip", timeout=3).read().decode().strip()
    except Exception:
        return "UNKNOWN"

def main():
    log("Starting provision.py")
    # interactive choice
    if os.environ.get("NONINTERACTIVE") == "1":
        profile = os.environ.get("PROFILE","php")
        log(f"NONINTERACTIVE running profile={profile}")
    else:
        print("Choose server purpose:")
        print(" 1. Static Web Server or Serverless")
        print(" 2. Proper Web Server (MariaDB, phpMyAdmin, email opt.)")
        print(" 3. NodeJS server")
        print(" 4. Django based server")
        print(" 5. Mailserver (advanced)")
        choice = input("Select number (e.g. 2): ").strip()
        map_ = {"1":"static","2":"php","3":"node","4":"django","5":"mail"}
        profile = map_.get(choice,"php")
        log(f"User selected profile={profile}")

    # ensure docker
    try:
        ensure_docker_installed()
    except Exception as e:
        log(f"Failed to ensure docker: {e}")
        raise

    # generate secrets
    envd = {}
    envd["MYSQL_ROOT_PASSWORD"] = generate_password(18)
    envd["MYSQL_PASSWORD"] = generate_password(16)
    envd["MYSQL_USER"] = "appuser"
    write_env(envd)

    # create files based on profile
    create_compose(profile)
    place_index()
    if profile == "php":
        create_php_files()

    # start stack
    try:
        bring_up()
    except Exception as e:
        log(f"docker compose failed: {e}")

    info = {"public_ip": public_ip(), "hostname": os.uname().nodename, "secrets": {"ENV": envd}, "ports":["80->nginx","8080->phpmyadmin"]}
    write_readme(info)
    log("Provisioning complete. README_SECURE.txt created.")
    print("\\nProvision complete. README_SECURE.txt created at", README)
    print("View logs:", LOG)

if __name__ == '__main__':
    main()
PY

# make it executable
chmod +x provision.py

# run it (interactive)
python3 -u provision.py 2>&1 | tee -a provision.log
