# 🚀 QuickAWS
### One-Click Automated AWS Server Provisioning  
*(Static, PHP, Node, Django, Mail, DB)*

QuickAWS is a lightweight automated provisioning system designed for teams deploying multiple AWS EC2 servers daily.  
It provides **one-command setup**, supports multiple server types, auto-generates credentials, and writes a secure summary file for easy SSH-only retrieval.

---

## ✨ Features

- **One-line install** on a fresh EC2 instance  
- **Supports multiple server profiles**:
  - Static Website Server
  - Full PHP Web Server (MariaDB + phpMyAdmin + nginx + PHP-FPM)
  - NodeJS Server
  - Django Server (Gunicorn + PostgreSQL)
  - Mail Server (optional)
- **Auto-generated strong passwords**
- **Secure credentials stored in `README_SECURE.txt`**
- Fully containerized (Docker + Docker Compose)
- Low-resource optimized (t4g.micro / t4g.small friendly)
- No manual config required

---

## 📦 Repository Structure

QuickAWS/
│
├── provision.py # Main provision engine
├── bootstrap.sh # Optional SSH-based cloner
├── index.php # Optional homepage (auto-copied to www/)
│
├── php/ # PHP Dockerfile (auto-generated if needed)
├── nginx/ # Nginx configs
├── www/ # Web root served by nginx/php
│
└── docker-compose.yml # Generated automatically based on profile


---

## ⚡ Quick Installation (Public Repo)

Run this on your EC2 server:

```bash
mkdir -p ~/app && \
curl -L https://github.com/aj-techsoul/QuickAWS/archive/refs/heads/main.tar.gz \
  | tar xz --strip-components=1 -C ~/app && \
cd ~/app && chmod +x provision.py && python3 provision.py
```

PHP stack (nginx + PHP + MariaDB + Adminer/phpMyAdmin)
```bash
mkdir -p ~/app && \
curl -L https://github.com/aj-techsoul/QuickAWS/archive/refs/heads/main.tar.gz \
  | tar xz --strip-components=1 -C ~/app && \
cd ~/app && chmod +x provision.py && \
NONINTERACTIVE=1 PROFILE=php python3 provision.py 2>&1 | tee provision_run.log
```
Static-only nginx (no PHP / DB)
```bash
mkdir -p ~/app && \
curl -L https://github.com/aj-techsoul/QuickAWS/archive/refs/heads/main.tar.gz \
  | tar xz --strip-components=1 -C ~/app && \
cd ~/app && chmod +x provision.py && \
NONINTERACTIVE=1 PROFILE=static python3 provision.py 2>&1 | tee provision_run.log
```

Replace "aj-techsoul" with your GitHub username or organization. if you fork this repo, for future use.




🔒 Quick Installation (Private Repo)
``` bash
mkdir -p ~/app && \
GITHUB_PAT="ghp_xxxxxxxxxxxxx"; \
curl -H "Authorization: token $GITHUB_PAT" \
  -L https://api.github.com/repos/OWNER/QuickAWS/tarball/main \
  | tar xz --strip-components=1 -C ~/app && \
unset GITHUB_PAT && \
cd ~/app && chmod +x provision.py && python3 provision.py
```
🖥️ Interactive Provisioning

When you run:

python3 provision.py


You will be asked to choose:

1. Static Web Server
2. PHP Web Server (MariaDB, phpMyAdmin, nginx)
3. NodeJS Server
4. Django Server (Gunicorn + PostgreSQL)
5. Mail Server


QuickAWS will then generate:
```bash
docker-compose.yml
service folders
credentials
```
a protected summary file:
README_SECURE.txt (chmod 600)

This file includes:
```bash
Public IP
Database credentials
App credentials
Ports & URLs
Timestamp
Server profile used
```
🔐 Security of README_SECURE.txt

Stored at:
```bash
/home/ec2-user/app/README_SECURE.txt
```

Protected with:
```bash
chmod 600 README_SECURE.txt
```

It can only be retrieved over SSH or SCP.

⚙️ Non-Interactive Mode (Automated Fleet Provisioning)
```bash
NONINTERACTIVE=1 PROFILE=php python3 provision.py
```

Available profiles:
```bash
static
php
node
django
mail
```

Example for Node:
```bash
NONINTERACTIVE=1 PROFILE=node python3 provision.py
```
🧹 Updating a Server
```bash
cd ~/app
python3 provision.py
```

Or:
```bash
NONINTERACTIVE=1 PROFILE=php python3 provision.py
```
🧰 Requirements

Amazon Linux 2 / Amazon Linux 2023 / Ubuntu
Python 3
Docker (QuickAWS installs it if missing)
Internet access for pulling Docker images

🏗️ Philosophy

Zero manual configuration
Fast (provisions in under 1 minute)
Secure (secrets never stored in repo)
Repeatable and scalable
Modular — easy to add new server profiles

📜 License

MIT

❤️ Credits

Created for teams needing rapid AWS provisioning at scale.
Supports up to hundreds of EC2 deployments per day.
Initiative by TECHSOUL (www.techsoul.in)
