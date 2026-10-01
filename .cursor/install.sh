#!/usr/bin/env bash
#
# Cloud Agent install script for root-kiosk.
#
# root-kiosk is a Debian-based kiosk OS image builder. The images are produced
# by ./build_raspberry_pi.sh, which downloads a Raspberry Pi OS image, expands it
# on a loop device, chroots into the arm64 root filesystem via
# qemu-user-static binfmt emulation and installs the kiosk skeleton.
# The image target is Raspberry Pi 5 (arm64).
#
# This script installs, idempotently and non-interactively:
#   1. The image-build toolchain used by the CI workflow (.github/workflows/main.yml).
#   2. shellcheck, so the shell scripts that make up most of the repo can be linted.
#   3. nginx + PHP, so the kiosk's local webserver (kiosk_skeleton/var/www/html)
#      can be run and validated during development.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
APT_LISTCHANGES_FRONTEND=none
export APT_LISTCHANGES_FRONTEND

# Keep existing conffiles on package upgrades so the install stays fully
# non-interactive (some base packages ship modified conffiles like /etc/fuse.conf).
APT_OPTS=(-y --no-install-recommends \
  -o Dpkg::Options::=--force-confdef \
  -o Dpkg::Options::=--force-confold)

sudo apt-get update -qq

# Image-build toolchain (mirrors the "Install dependencies" step in CI) plus
# the linter (ShellCheck) and nginx/php for exercising the local webserver.
sudo apt-get install "${APT_OPTS[@]}" \
  libguestfs-tools \
  qemu-utils \
  qemu-system-arm \
  qemu-efi-aarch64 \
  qemu-user-static \
  binfmt-support \
  rsync \
  sudo \
  wget \
  ca-certificates \
  xz-utils \
  pigz \
  mount \
  dosfstools \
  libarchive-tools \
  zerofree \
  util-linux \
  e2fsprogs \
  fdisk \
  shellcheck \
  nginx \
  php-fpm \
  php-cli \
  python3-venv \
  python3-pip

echo "root-kiosk Cloud Agent environment ready."
