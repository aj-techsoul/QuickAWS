#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import random
import string
from pathlib import Path
import shutil
import traceback
import threading
import itertools
from typing import Union
from contextlib import contextmanager

LOGFILE = Path("provision.log")


# ---------- Logging & helpers ----------

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {msg}"
    print(line)
    try:
        with LOGFILE.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run(cmd: str, check: bool = True):
    log(f"> {cmd}")
    res = subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if res.stdout:
        for line in res.stdout.splitlines():
            log("  " + line)
    if check and res.returncode != 0:
        raise RuntimeError(
            f"Command failed ({res.returncode}): {cmd}\nOutput:\n{res.stdout}"
        )
    return res


def which(cmd: str):
    from shutil import which as _which
    return _which(cmd)


def safe_mkdir(path: Union[str, Path], mode: int = 0o755):
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    try:
        p.chmod(mode)
    except Exception:
        pass


def random_pw(n: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits + "-_!@"
    return "".join(random.choice(alphabet) for _ in range(n))


TOTAL_STEPS = 0
CURRENT_STEP = 0


def set_total_steps(n: int):
    global TOTAL_STEPS, CURRENT_STEP
    TOTAL_STEPS = n
    CURRENT_STEP = 0


@contextmanager
def step(title: str):
    global CURRENT_STEP, TOTAL_STEPS
    CURRENT_STEP += 1
    if TOTAL_STEPS:
        prefix = f"[{CURRENT_STEP}/{TOTAL_STEPS}] {title}"
    else:
        prefix = title
    log(prefix)
    try:
        yield
        log(prefix + " ... done")
    except Exception as e:
        log(prefix + f" ... FAILED: {e}")
        raise


def run_with_spinner(cmd: str, label: str = "", check: bool = True):
    stop_event = threading.Event()

    def spinner():
        chars = "|/-\\"
        it = itertools.cycle(chars)
        text = label or cmd
        while not stop_event.is_set():
            sys.stdout.write("\r" + text + " " + next(it))
            sys.stdout.flush()
            time.sleep(0.15)
        sys.stdout.write("\r" + text + " ... done\n")
        sys.stdout.flush()

    t = threading.Thread(target=spinner, daemon=True)
    t.start()
    try:
        log(f"> {cmd}")
        res = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if res.stdout:
            for line in res.stdout.splitlines():
                log("  " + line)
        if check and res.returncode != 0:
            raise RuntimeError(
                f"Command failed ({res.returncode}): {cmd}\nOutput:\n{res.stdout}"
            )
        return res
    finally:
        stop_event.set()
        t.join()


# ---------- OS / Docker setup ----------

def detect_os_arch():
    os_release = {}
    if Path("/etc/os-release").exists():
        for ln in open("/etc/os-release"):
            ln = ln.strip()
            if "=" in ln:
                k, v = ln.split("=", 1)
                os_release[k] = v.strip().strip('"')
    distro = os_release.get("ID", "").lower()
    like = os_release.get("ID_LIKE", "").lower()
    version = os_release.get("VERSION_ID", "")
    arch = os.uname().machine
    return {"distro": distro, "like": like, "version": version, "arch": arch, "os_release": os_release}


def ensure_docker_installed():
    log("Checking Docker/Compose presence...")

    # Docker
    if which("docker"):
        run("docker --version", check=False)
        log("Docker binary present.")
    else:
        info = detect_os_arch()
        distro = info["distro"]
        version = info["version"]
        log(f"Detected distro: {distro} version: {version} arch: {info['arch']}")
        try:
            if "amazon" in distro and version.startswith("2023"):
                run("sudo dnf -y update")
                run("sudo dnf -y install docker")
                run("sudo systemctl enable --now docker")
            elif "amzn" in distro or "amazon" in distro:
                try:
                    run("sudo amazon-linux-extras enable docker || true", check=False)
                except Exception:
                    log("amazon-linux-extras not available; continuing.")
                run("sudo yum -y update")
                run("sudo yum -y install -y docker || true")
                run("sudo systemctl enable --now docker")
            elif any(x in distro for x in ("ubuntu", "debian", "raspbian", "pop")):
                run("sudo apt-get update -y")
                run("sudo apt-get install -y ca-certificates curl gnupg lsb-release")
                run("sudo mkdir -p /etc/apt/keyrings")
                run("curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg")
                run(
                    'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] '
                    'https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" '
                    '| sudo tee /etc/apt/sources.list.d/docker.list > /dev/null'
                )
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
                    raise RuntimeError("Unable to detect package manager to install Docker.")
        except Exception as e:
            log("Docker installation encountered error: " + str(e))
            raise

    # docker group
    try:
        current_user = os.environ.get("USER") or os.environ.get("LOGNAME") or os.getlogin()
    except Exception:
        current_user = "ec2-user"
    log(f"Adding user {current_user} to docker group (if not already)")
    run(f"sudo usermod -aG docker {current_user} || true", check=False)
    run("sudo systemctl enable --now docker || true", check=False)

    # docker-compose
    compose_ok = False
    try:
        res = subprocess.run(
            "docker compose version",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
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
        log("Installed docker-compose binary to /usr/local/bin/docker-compose")

    run("docker --version", check=False)
    run("docker-compose --version || docker compose version || true", check=False)
    time.sleep(1)


# ---------- App config writers ----------

def safe_write_dotenv(rootpw=None, userpw=None, user="appuser"):
    if not rootpw:
        rootpw = random_pw()
    if not userpw:
        userpw = random_pw()
    env_path = Path(".env")
    content = [
        f"MYSQL_ROOT_PASSWORD={rootpw}",
        "MYSQL_DATABASE=appdb",
        f"MYSQL_USER={user}",
        f"MYSQL_PASSWORD={userpw}",
    ]
    env_path.write_text("\n".join(content) + "\n")
    try:
        env_path.chmod(0o600)
    except Exception:
        pass
    log(".env written with generated credentials")
    return rootpw, userpw, user


def write_index_php():
    www = Path("www")
    www.mkdir(parents=True, exist_ok=True)
    target = www / "index.php"
    if Path("index.php").exists():
        shutil.copy("index.php", target)
        log("Copied index.php into www/")
    else:
        content = """<?php
echo "<h2>QuickAWS: PHP stack is running</h2>\\n";
echo "<p>Server time: " . date('c') . "</p>\\n";
?>"""
        target.write_text(content)
        log("Wrote default www/index.php")


def write_index_static():
    www = Path("www")
    www.mkdir(parents=True, exist_ok=True)
    target = www / "index.html"
    if target.exists():
        log("Static index.html already exists, leaving as-is.")
        return
    content = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>QuickAWS Static Site</title>
</head>
<body>
  <h2>QuickAWS: Static nginx server is running</h2>
  <p>Deployed at: {time}</p>
</body>
</html>
""".format(time=time.strftime("%Y-%m-%d %H:%M:%S"))
    target.write_text(content)
    log("Wrote default www/index.html")


def write_nginx_conf_php():
    conf_dir = Path("nginx/conf.d")
    conf_dir.mkdir(parents=True, exist_ok=True)
    conf = r"""
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
}
"""
    conf_path = conf_dir / "default.conf"
    conf_path.write_text(conf)
    try:
        conf_path.chmod(0o644)
    except Exception:
        pass
    log("Wrote nginx/conf.d/default.conf for PHP profile")


def write_nginx_conf_static():
    conf_dir = Path("nginx/conf.d")
    conf_dir.mkdir(parents=True, exist_ok=True)
    conf = r"""
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html index.htm;

    location / {
        try_files $uri $uri/ =404;
    }
}
"""
    conf_path = conf_dir / "default.conf"
    conf_path.write_text(conf)
    try:
        conf_path.chmod(0o644)
    except Exception:
        pass
    log("Wrote nginx/conf.d/default.conf for static profile")


def generate_php_compose(arch: str) -> str:
    is_arm = arch and ("aarch64" in arch or arch.startswith("arm"))

    if is_arm:
        # ARM-safe DB UI: Adminer, bound to localhost:8080
        db_ui_block = """  adminer:
    image: adminer:latest
    restart: unless-stopped
    environment:
      ADMINER_DEFAULT_SERVER: db
    ports:
      - "127.0.0.1:8080:8080"
"""
    else:
        # x86: phpMyAdmin, bound to localhost:8080
        db_ui_block = """  phpmyadmin:
    image: phpmyadmin/phpmyadmin:latest
    restart: unless-stopped
    environment:
      PMA_HOST: db
      PMA_USER: root
      PMA_PASSWORD: "${MYSQL_ROOT_PASSWORD}"
    ports:
      - "127.0.0.1:8080:80"
"""

    compose = (
        "services:\n"
        "  nginx:\n"
        "    image: nginx:stable-alpine\n"
        "    restart: unless-stopped\n"
        "    volumes:\n"
        "      - ./www:/var/www/html:ro\n"
        "      - ./nginx/conf.d:/etc/nginx/conf.d:ro\n"
        "      - /etc/letsencrypt:/etc/letsencrypt:ro\n"
        "      - /etc/nginx/ssl:/etc/nginx/ssl:ro\n"
        "    ports:\n"
        '      - "80:80"\n'
        "    depends_on:\n"
        "      - php\n"
        "\n"
        "  php:\n"
        "    image: php:8.1-fpm-alpine\n"
        "    restart: unless-stopped\n"
        "    volumes:\n"
        "      - ./www:/var/www/html\n"
        "    depends_on:\n"
        "      - db\n"
        "\n"
        "  db:\n"
        "    image: mariadb:10.5\n"
        "    restart: unless-stopped\n"
        "    environment:\n"
        '      MYSQL_ROOT_PASSWORD: "${MYSQL_ROOT_PASSWORD}"\n'
        "      MYSQL_DATABASE: appdb\n"
        "      MYSQL_USER: appuser\n"
        '      MYSQL_PASSWORD: "${MYSQL_PASSWORD}"\n'
        "    volumes:\n"
        "      - db_data:/var/lib/mysql\n"
        "\n"
        + db_ui_block +
        "\n"
        "volumes:\n"
        "  db_data:\n"
    )
    return compose


def generate_static_compose() -> str:
    compose = (
        "services:\n"
        "  nginx:\n"
        "    image: nginx:stable-alpine\n"
        "    restart: unless-stopped\n"
        "    volumes:\n"
        "      - ./www:/usr/share/nginx/html:ro\n"
        "    ports:\n"
        '      - "80:80"\n'
        "\n"
        "volumes: {}\n"
    )
    return compose


def write_file(path: Union[str, Path], content: str, mode: int = 0o644):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    try:
        p.chmod(mode)
    except Exception:
        pass
    log(f"Wrote {path} ({len(content)} bytes)")


def validate_compose_yaml():
    try:
        run("sudo docker-compose config", check=False)
    except Exception as e:
        log("docker-compose config failed (non-fatal): " + str(e))


def try_docker_compose_up():
    cmds = [
        ("docker compose up -d", "Starting containers (docker compose up -d)"),
        ("docker-compose up -d", "Starting containers (docker-compose up -d)"),
        ("sudo docker-compose up -d --remove-orphans --build",
         "Starting containers as root (sudo docker-compose up -d --build)"),
    ]
    last_out = ""
    for cmd, label in cmds:
        try:
            log(f"Attempting compose with: {cmd}")
            res = run_with_spinner(cmd, label=label, check=False)
            if res.returncode == 0:
                log("Compose started successfully.")
                return True
            else:
                last_out = getattr(res, "stdout", "") or ""
        except Exception as e:
            last_out = str(e)
    log("All compose attempts failed. Last output:\n" + last_out)
    return False


def write_readme_secure(env_path=".env", out_path="README_SECURE.txt"):
    try:
        env = {}
        p = Path(env_path)
        if p.exists():
            for line in p.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()

        pubip = ""
        try:
            pubip = subprocess.check_output(
                ["curl", "-sS", "http://169.254.169.254/latest/meta-data/public-ipv4"],
                text=True,
                timeout=2,
            ).strip()
        except Exception:
            pass

        content = f"""=== QUICKAWS PROVISION SUMMARY ===
Time: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}
Public IP: {pubip}
Hostname: {os.uname().nodename}

Generated credentials (store securely):
{{
  "MYSQL_ROOT_PASSWORD": "{env.get('MYSQL_ROOT_PASSWORD', '')}",
  "MYSQL_USER": "{env.get('MYSQL_USER', 'appuser')}",
  "MYSQL_PASSWORD": "{env.get('MYSQL_PASSWORD', '')}"
}}

Ports:
 - 80   -> nginx
 - 8080 -> DB UI (Adminer/phpMyAdmin, bound to localhost by default)

Notes:
 - README_SECURE.txt is chmod 600. Download via SSH (scp) only.
"""
        out = Path(out_path)
        out.write_text(content)
        try:
            out.chmod(0o600)
        except Exception:
            pass
        log(f"Wrote {out_path} (600)")
    except Exception as e:
        log("Failed to write README_SECURE.txt: " + str(e))


# ---------- Profile selection ----------

def choose_profile() -> str:
    noninteractive = os.environ.get("NONINTERACTIVE", "").lower() in ("1", "true", "yes")
    default_profile = os.environ.get("PROFILE", "php").lower()

    profiles = {
        "1": "static",
        "2": "php",
        "3": "node",
        "4": "django",
        "5": "mail",
        "6": "tls",
    }

    if noninteractive:
        if default_profile in profiles.values():
            log(f"NONINTERACTIVE mode: profile={default_profile}")
            return default_profile
        else:
            log(f"NONINTERACTIVE mode: unknown PROFILE={default_profile}, defaulting to php")
            return "php"

    print("Choose server purpose:")
    print(" 1. Static Web Server")
    print(" 2. Proper Web Server (PHP + MariaDB + DB UI)")
    print(" 3. NodeJS server (not implemented yet)")
    print(" 4. Django based server (not implemented yet)")
    print(" 5. Mailserver (advanced, not implemented yet)")
    print(" 6. Configure domain + HTTPS for existing stack")

    choice = input("Select number (default 2): ").strip() or "2"
    profile = profiles.get(choice, "php")
    log(f"User selected profile={profile}")
    return profile


# ---------- TLS helper (Option 6) ----------

def detect_stack_type_from_compose(compose_path: Union[str, Path] = "docker-compose.yml") -> str:
    p = Path(compose_path)
    if not p.exists():
        raise RuntimeError("docker-compose.yml not found; cannot detect stack type.")
    text = p.read_text()
    for line in text.splitlines():
        if line.strip().startswith("php:"):
            return "php"
    return "static"


def generate_tls_nginx_conf(domain: str, php_stack: bool) -> str:
    domain = domain.strip()
    server_names = f"{domain} www.{domain}"

    base_redirect = f"""
server {{
    listen 80;
    server_name {server_names};
    return 301 https://$host$request_uri;
}}
"""

    if php_stack:
        ssl_block = f"""
server {{
    listen 443 ssl;
    server_name {server_names};

    root /var/www/html;
    index index.php index.html index.htm;

    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;

    location / {{
        try_files $uri $uri/ /index.php?$query_string;
    }}

    location ~ \.php$ {{
        fastcgi_pass php:9000;
        fastcgi_index index.php;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    }}
}}
"""
    else:
        ssl_block = f"""
server {{
    listen 443 ssl;
    server_name {server_names};

    root /usr/share/nginx/html;
    index index.html index.htm;

    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;

    location / {{
        try_files $uri $uri/ =404;
    }}
}}
"""

    return base_redirect + "\n" + ssl_block


def obtain_cert_with_docker(domain: str, email: str):
    webroot = Path("www").absolute()
    webroot.mkdir(parents=True, exist_ok=True)

    cmd = (
        f'sudo docker run --rm '
        f'-v "/etc/letsencrypt:/etc/letsencrypt" '
        f'-v "{webroot}:/var/www/html" '
        f'certbot/certbot certonly --webroot -w /var/www/html '
        f'-d {domain} -d www.{domain} '
        f'--agree-tos --non-interactive '
    )
    if email:
        cmd += f'-m "{email}" '
    else:
        cmd += "--register-unsafely-without-email "

    run_with_spinner(cmd, label=f"Issuing Let's Encrypt cert for {domain}", check=True)


def setup_tls_for_existing_stack():
    compose_path = Path("docker-compose.yml")
    if not compose_path.exists():
        msg = "docker-compose.yml not found. Run profile 'php' or 'static' first."
        log(msg)
        print("ERROR:", msg)
        sys.exit(1)

    noninteractive = os.environ.get("NONINTERACTIVE", "").lower() in ("1", "true", "yes")

    if noninteractive:
        domain = os.environ.get("DOMAIN", "").strip()
        email = os.environ.get("EMAIL", "").strip()
        if not domain:
            msg = "NONINTERACTIVE TLS requires DOMAIN environment variable."
            log(msg)
            print("ERROR:", msg)
            sys.exit(1)
        log(f"NONINTERACTIVE TLS for domain={domain} email={email or '(none)'}")
    else:
        domain = input("Enter your primary domain (e.g. example.com): ").strip()
        if not domain:
            print("Domain cannot be empty.")
            sys.exit(1)
        email = input("Enter email for Let's Encrypt (or leave blank to skip): ").strip()

    set_total_steps(3)

    with step("Ensuring Docker is installed and running"):
        ensure_docker_installed()

    with step(f"Obtaining Let's Encrypt certificate for {domain}"):
        try:
            obtain_cert_with_docker(domain, email)
        except Exception as e:
            log(f"Certbot (docker) failed: {e}")
            print("ERROR: Failed to obtain certificate. Check provision.log for details.")
            sys.exit(1)

    with step("Writing HTTPS nginx config and restarting nginx"):
        stack_type = "php"
        try:
            stack_type = detect_stack_type_from_compose()
        except Exception as e:
            log("Could not detect stack type from compose: " + str(e))

        if stack_type != "php":
            msg = "TLS helper currently fully tested for PHP stack only. Detected non-PHP stack."
            log(msg)
            print("ERROR:", msg)
            sys.exit(1)

        conf = generate_tls_nginx_conf(domain, php_stack=True)
        write_file("nginx/conf.d/default.conf", conf)
        run_with_spinner(
            "sudo docker-compose restart nginx",
            label="Restarting nginx with new TLS config",
            check=False,
        )
        log("Nginx restarted with HTTPS configuration.")

    log("TLS configuration complete.")


# ---------- Main ----------

def main():
    try:
        log("=== QuickAWS provisioner starting ===")
        info = detect_os_arch()
        arch = info["arch"]
        log(f"Detected arch: {arch}, distro: {info['distro']}")

        profile = choose_profile()

        # TLS-only path (Option 6)
        if profile == "tls":
            setup_tls_for_existing_stack()
            log("Provisioning complete (TLS).")
            log("Log file: " + str(LOGFILE.absolute()))
            return

        # Only php/static are implemented as full stacks right now
        if profile not in ("php", "static"):
            log(f"Profile '{profile}' is not implemented yet. Please use 'php', 'static' or 'tls' for now.")
            print(f"Profile '{profile}' is not implemented yet. Please use 'php', 'static' or 'tls' for now.")
            sys.exit(1)

        if profile == "php":
            set_total_steps(4)
        else:
            set_total_steps(3)

        with step("Ensuring Docker is installed and running"):
            ensure_docker_installed()

        with step("Preparing configuration and files"):
            safe_mkdir("www")
            safe_mkdir("nginx/conf.d")

            if profile == "php":
                # Ensure .env with passwords
                if not Path(".env").exists():
                    safe_write_dotenv()
                else:
                    data = {}
                    for ln in Path(".env").read_text().splitlines():
                        if "=" in ln:
                            k, v = ln.split("=", 1)
                            data[k.strip()] = v.strip()
                    rootpw = data.get("MYSQL_ROOT_PASSWORD") or random_pw()
                    userpw = data.get("MYSQL_PASSWORD") or random_pw()
                    user = data.get("MYSQL_USER") or "appuser"
                    safe_write_dotenv(rootpw=rootpw, userpw=userpw, user=user)

            if profile == "php":
                write_index_php()
                write_nginx_conf_php()
                compose_text = generate_php_compose(arch)
            else:
                write_index_static()
                write_nginx_conf_static()
                compose_text = generate_static_compose()

            write_file("docker-compose.yml", compose_text)
            validate_compose_yaml()

        with step("Starting Docker stack (this may take a few minutes on first run)"):
            ok = try_docker_compose_up()
            if not ok:
                log("Compose failed. You may need to run: sudo docker-compose up -d --build")
                print("WARNING: docker-compose up failed. Check provision.log and try: sudo docker-compose up -d --build")
            else:
                log("Compose up attempted; verify with 'sudo docker ps -a'.")

        if profile == "php":
            with step("Writing secure summary (README_SECURE.txt)"):
                write_readme_secure()

        log("Provisioning complete.")
        if profile == "php":
            log("Secure README: " + str(Path("README_SECURE.txt").absolute()))
        log("Log file: " + str(LOGFILE.absolute()))

    except KeyboardInterrupt:
        log("Provisioner interrupted by user (Ctrl+C).")
        sys.exit(1)
    except Exception:
        log("Provisioner failed with exception:")
        log(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
