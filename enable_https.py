#!/usr/bin/env python3
"""
QuickAWS: Add domain + HTTPS to an existing PHP stack.

Use AFTER provision.py has already created and started the stack.

Features:
- Asks (or reads env) for:
  - DOMAIN  (e.g. example.com)
  - LE_EMAIL (email for Let's Encrypt)
- Installs certbot (best effort) using yum/dnf/apt.
- Obtains Let's Encrypt cert using standalone mode on port 80.
- Updates:
  - nginx/conf.d/default.conf  -> HTTP->HTTPS redirect + HTTPS server
  - docker-compose.yml         -> exposes 443:443 for nginx (if not already)
- Restarts nginx container.

Usage:
  cd ~/app
  python3 enable_https.py

Environment (optional, for non-interactive):
  DOMAIN=example.com LE_EMAIL=you@example.com python3 enable_https.py
"""

import os
import sys
import subprocess
import time
from pathlib import Path
import shutil


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print("%s %s" % (ts, msg))


def run(cmd, check=True):
    log("> " + cmd)
    res = subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if res.stdout:
        for line in res.stdout.splitlines():
            print("  " + line)
    if check and res.returncode != 0:
        raise RuntimeError("Command failed (%s): %s\n%s" % (res.returncode, cmd, res.stdout))
    return res


def which(cmd):
    from shutil import which as _which
    return _which(cmd)


def detect_os():
    os_release = {}
    p = Path("/etc/os-release")
    if p.exists():
        for ln in p.read_text().splitlines():
            ln = ln.strip()
            if "=" in ln:
                k, v = ln.split("=", 1)
                os_release[k] = v.strip().strip('"')
    distro = os_release.get("ID", "").lower()
    like = os_release.get("ID_LIKE", "").lower()
    version = os_release.get("VERSION_ID", "")
    return {"distro": distro, "like": like, "version": version, "os_release": os_release}


def ensure_certbot():
    """Install certbot with yum/dnf/apt if needed."""
    if which("certbot"):
        log("certbot already installed.")
        return

    info = detect_os()
    distro = info["distro"]
    log("Certbot missing. Detected distro: %s" % distro)

    try:
        if "amzn" in distro or "amazon" in distro:
            # Amazon Linux (2 / 2023) – try dnf, then yum
            if which("dnf"):
                run("sudo dnf install -y certbot", check=False)
            elif which("yum"):
                run("sudo yum install -y certbot", check=False)
        elif any(x in distro for x in ("ubuntu", "debian", "raspbian")):
            run("sudo apt-get update -y", check=False)
            run("sudo apt-get install -y certbot", check=False)
        else:
            # generic fallback
            if which("dnf"):
                run("sudo dnf install -y certbot", check=False)
            elif which("yum"):
                run("sudo yum install -y certbot", check=False)
            elif which("apt-get"):
                run("sudo apt-get update -y", check=False)
                run("sudo apt-get install -y certbot", check=False)
    except Exception as e:
        log("Error while trying to install certbot: %s" % e)

    if not which("certbot"):
        raise SystemExit("certbot not found after install attempts. Install it manually and re-run.")


def get_domain_email():
    domain = os.environ.get("DOMAIN", "").strip()
    email = os.environ.get("LE_EMAIL", "").strip()

    if not domain:
        domain = raw_input_py3("Enter your domain (e.g. example.com): ").strip()
    if not email:
        email = raw_input_py3("Email for Let's Encrypt (expiry notices etc.): ").strip()

    if not domain:
        raise SystemExit("Domain is required.")
    if "." not in domain:
        raise SystemExit("Domain '%s' does not look valid." % domain)
    if not email or "@" not in email:
        raise SystemExit("Valid email is required for Let's Encrypt.")

    return domain, email


def raw_input_py3(prompt):
    try:
        return input(prompt)
    except EOFError:
        return ""


def obtain_certificate(domain, email):
    """
    Stop nginx container, run certbot standalone to get cert for:
      - domain
      - www.domain
    """
    log("Stopping nginx container (to free port 80 for certbot standalone)...")
    run("sudo docker-compose stop nginx || true", check=False)

    ensure_certbot()

    log("Requesting certificate from Let's Encrypt...")
    cmd = (
        "sudo certbot certonly --standalone "
        "--non-interactive --agree-tos "
        "-m \"%s\" -d \"%s\" -d \"www.%s\" "
        "--preferred-challenges http"
    ) % (email, domain, domain)
    run(cmd, check=True)
    log("Certificate request finished. If it failed, check above logs.")

    # basic check that cert directory exists
    cert_dir = Path("/etc/letsencrypt/live") / domain
    if not cert_dir.exists():
        raise SystemExit("Certificate directory %s not found. certbot may have failed." % cert_dir)
    log("Cert directory looks good: %s" % cert_dir)
    return str(cert_dir)


def patch_docker_compose_for_443(compose_path="docker-compose.yml"):
    """
    Ensure nginx service has ports:
      - "80:80"
      - "443:443"
    Simple string-based patch (we know the structure QuickAWS generates).
    """
    p = Path(compose_path)
    if not p.exists():
        raise SystemExit("docker-compose.yml not found in current directory.")

    s = p.read_text()

    # only patch if 443 not already present
    if '  nginx:' not in s:
        log("No nginx service found in docker-compose.yml, skipping 443 patch.")
        return

    if '"443:443"' in s:
        log("443 mapping already present in docker-compose.yml, leaving as-is.")
        return

    # find the ports block inside nginx
    needle = '  nginx:\n'
    idx = s.find(needle)
    if idx == -1:
        log("Could not find nginx block in docker-compose.yml, skipping 443 patch.")
        return

    # from nginx block, find "ports:" under it
    nginx_block = s[idx:]
    ports_idx = nginx_block.find("    ports:")
    if ports_idx == -1:
        log("nginx service has no ports: block, skipping 443 patch.")
        return

    # from ports: to next line after it
    ports_start = idx + ports_idx
    sub = s[ports_start:]
    # look for the "80:80" line in that ports block
    port80 = '      - "80:80"'
    sub_idx = sub.find(port80)
    if sub_idx == -1:
        log("Could not find '80:80' line under nginx ports, skipping 443 patch.")
        return

    # insert 443 line after 80:80
    insert_pos = ports_start + sub_idx + len(port80)
    new_s = s[:insert_pos] + '\n      - "443:443"' + s[insert_pos:]
    backup = compose_path + ".bak_https"
    shutil.copy(p, backup)
    p.write_text(new_s)
    log("Patched docker-compose.yml to add 443:443 (backup at %s)" % backup)


def write_nginx_https_conf(domain, conf_path="nginx/conf.d/default.conf"):
    """
    Overwrite nginx/conf.d/default.conf with HTTP->HTTPS redirect + HTTPS server
    using the Let’s Encrypt cert for the given domain.
    """
    p = Path(conf_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    conf = """server {{
    listen 80;
    server_name {domain} www.{domain};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl http2;
    server_name {domain} www.{domain};

    root /var/www/html;
    index index.php index.html index.htm;

    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

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
""".format(domain=domain)

    p.write_text(conf)
    try:
        p.chmod(0o644)
    except Exception:
        pass
    log("Wrote HTTPS nginx config to %s" % conf_path)


def restart_nginx():
    log("Recreating nginx container with new config and 443 port...")
    run("sudo docker-compose up -d --force-recreate nginx", check=True)
    run("sudo docker ps -a --format 'table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}'", check=False)


def main():
    log("=== QuickAWS enable_https starting ===")

    # ensure we are in app dir with docker-compose.yml
    if not Path("docker-compose.yml").exists():
        raise SystemExit("docker-compose.yml not found. Run this from your QuickAWS app directory (e.g. ~/app).")

    domain, email = get_domain_email()
    log("Using domain=%s email=%s" % (domain, email))

    print("")
    print("IMPORTANT:")
    print("  - Make sure your domain has an A record pointing to THIS server's public IP.")
    print("  - Port 80 must be open in the security group.")
    ans = raw_input_py3("Continue and request Let's Encrypt cert now? [y/N]: ").strip().lower()
    if ans not in ("y", "yes"):
        raise SystemExit("Aborted by user.")

    cert_dir = obtain_certificate(domain, email)

    log("Updating nginx config for HTTPS...")
    write_nginx_https_conf(domain)

    log("Ensuring docker-compose.yml publishes 443 for nginx...")
    patch_docker_compose_for_443()

    restart_nginx()

    log("Done. Test:")
    log("  http://%s/  -> should redirect to https://" % domain)
    log("  https://%s/" % domain)
    log("Certificate directory: %s" % cert_dir)
    log("=== QuickAWS enable_https finished ===")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
