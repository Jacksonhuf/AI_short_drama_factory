#!/usr/bin/env bash
set -euo pipefail

# Prepares a CentOS Stream host for the low-resource Jellyfish Docker stack.
if [[ "${EUID}" -ne 0 ]]; then
  echo "This script must run as root." >&2
  exit 1
fi

ensure_kernel_modules() {
  # Load Docker bridge-network dependencies and persist them across VPS reboots.
  local module
  local modules=(overlay br_netfilter nf_nat ip_tables iptable_nat xt_addrtype)

  for module in "${modules[@]}"; do
    modprobe "${module}"
  done

  printf '%s\n' "${modules[@]}" > /etc/modules-load.d/jellyfish-docker.conf
  cat > /etc/sysctl.d/99-jellyfish-docker.conf <<'EOF'
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward = 1
EOF
  sysctl --system
}

ensure_docker() {
  # Install and start Docker Engine plus the Compose plugin.
  if ! command -v docker >/dev/null 2>&1; then
    dnf install -y dnf-plugins-core ca-certificates curl
    dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
    dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  fi
  if ! systemctl enable --now docker; then
    systemctl status docker --no-pager --full || true
    journalctl -u docker --no-pager --lines=100 || true
    exit 1
  fi
}

ensure_firewall() {
  # Expose only HTTP and HTTPS when firewalld is active.
  if systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-service=http
    firewall-cmd --permanent --add-service=https
    firewall-cmd --reload
  fi
}

ensure_swap() {
  # Create persistent 2GiB swap to reduce OOM risk on the 2GiB VPS.
  if swapon --show --noheadings | grep -q .; then
    return
  fi
  fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=progress
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile swap swap defaults 0 0' >> /etc/fstab
  sysctl -w vm.swappiness=10
  grep -q '^vm.swappiness=' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
}

ensure_kernel_modules
ensure_docker
ensure_firewall
ensure_swap
