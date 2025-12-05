import os
import subprocess
import sys
import time

def run(cmd, check=True):
    print(f"> {cmd}")
    res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(res.stdout)
    if check and res.returncode != 0:
        raise SystemExit(f"Command failed: {cmd}")

def shutil_which(name):
    from shutil import which
    return which(name)

def ensure_docker_installed():
    """
    Auto-detect distro and install Docker + Docker Compose (plugin or standalone binary).
    Idempotent and supports Amazon Linux 2, Amazon Linux 2023, Ubuntu/Debian and fallbacks.
    """
    # If docker already present, nothing to do (but still ensure compose)
    if shutil_which("docker"):
        print("Docker already installed.")
    else:
        # detect OS
        os_release = {}
        if os.path.exists("/etc/os-release"):
            for line in open("/etc/os-release"):
                line=line.strip()
                if "=" in line:
                    k,v = line.split("=",1)
                    os_release[k] = v.strip().strip('"')
        distro = os_release.get("ID", "").lower()
        distro_like = os_release.get("ID_LIKE", "").lower()
        arch = os.uname().machine

        print(f"Detected distro: {distro} (like: {distro_like}), arch: {arch}")

        try:
            # Amazon Linux 2023 path
            if "amazon" in distro and os_release.get("VERSION_ID","").startswith("2023"):
                run("sudo dnf -y update")
                run("sudo dnf -y install docker")
                run("sudo systemctl enable --now docker")

            # Amazon Linux 2 path (amazon-linux-extras available on many AL2 images)
            elif "amzn" in distro or "amazon" in distro or "amazon linux" in distro or "amzn" in os_release.get("NAME","").lower():
                # best-effort: try amazon-linux-extras, fallback to yum install
                try:
                    run("sudo amazon-linux-extras enable docker || true")
                except Exception:
                    print("amazon-linux-extras not present or failed; continuing with yum")
                run("sudo yum -y update")
                run("sudo yum -y install -y docker || true")
                # enable and start
                run("sudo systemctl enable --now docker")

            # Debian/Ubuntu family
            elif "ubuntu" in distro or "debian" in distro or "raspbian" in distro or "pop" in distro:
                run("sudo apt-get update -y")
                # Install prerequisites then docker
                run("sudo apt-get install -y ca-certificates curl gnupg lsb-release")
                run("sudo mkdir -p /etc/apt/keyrings")
                run("curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg")
                run('echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null')
                run("sudo apt-get update -y")
                run("sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin")
                run("sudo systemctl enable --now docker")

            else:
                # Fallback: try distro package manager heuristics
                print("Unknown or unsupported distro detected; attempting generic install via package manager.")
                # Try yum, apt, or dnf in that order
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
                    raise SystemExit("Unable to detect package manager to install Docker. Install Docker manually.")
        except Exception as e:
            print("Docker install encountered error:", e)
            raise

    # at this point docker should be installed and running (or present)
    # ensure current user is allowed to run docker without sudo
    try:
        current_user = os.environ.get("USER") or os.environ.get("LOGNAME") or os.getlogin()
    except Exception:
        current_user = "ec2-user"
    print(f"Adding user {current_user} to docker group (if not already member)")
    run(f"sudo usermod -aG docker {current_user} || true", check=False)

    # Install docker compose if not available as plugin/binary
    compose_installed = False
    # check for docker compose v2 plugin: `docker compose version`
    try:
        res = subprocess.run("docker compose version", shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if res.returncode == 0:
            print("Docker Compose (v2 plugin) available.")
            compose_installed = True
    except Exception:
        pass

    # Check for old docker-compose binary
    if not compose_installed and shutil_which("docker-compose"):
        print("docker-compose binary already present.")
        compose_installed = True

    if not compose_installed:
        # Try to install plugin via package manager (best-effort)
        if shutil_which("dnf"):
            try:
                run("sudo dnf -y install docker-compose-plugin || true")
                # re-check
                res = subprocess.run("docker compose version", shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                if res.returncode == 0:
                    print("Installed docker compose plugin via dnf.")
                    compose_installed = True
            except Exception:
                pass
        if not compose_installed and shutil_which("yum"):
            try:
                run("sudo yum -y install docker-compose-plugin || true")
                res = subprocess.run("docker compose version", shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                if res.returncode == 0:
                    compose_installed = True
            except Exception:
                pass
        if not compose_installed and shutil_which("apt-get"):
            try:
                run("sudo apt-get install -y docker-compose-plugin || true")
                res = subprocess.run("docker compose version", shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                if res.returncode == 0:
                    compose_installed = True
            except Exception:
                pass

    if not compose_installed:
        # Install standalone compose binary with correct architecture
        arch = os.uname().machine
        if arch == "aarch64":
            binurl = "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64"
        else:
            binurl = "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64"
        print(f"Downloading docker-compose from {binurl}")
        run(f"sudo curl -L {binurl} -o /usr/local/bin/docker-compose")
        run("sudo chmod +x /usr/local/bin/docker-compose")
        # create shim to allow "docker compose" fallback if plugin not present (optional)
        if not shutil_which("docker-compose"):
            print("docker-compose binary installed.")
        else:
            print("docker-compose binary present.")

    # Refresh group membership for current shell so docker can be used without logout
    print("Refreshing group membership so current shell can use docker without logout (newgrp).")
    try:
        # exec newgrp in a subshell and return to caller - non-blocking; safe best-effort
        subprocess.run("exec newgrp docker", shell=True, check=False)
    except Exception:
        pass

    # quick checks
    run("docker --version", check=False)
    # prefer docker compose v2 check
    run("docker compose version || docker-compose --version", check=False)

    # small pause to allow docker to initialize
    time.sleep(2)
