#!/usr/bin/env bash
# Install Python 3.11 on Ubuntu 22.04.
#
# WHY THIS SCRIPT EXISTS
# ----------------------
# Ubuntu 22.04 (jammy) does NOT ship a release build of Python 3.11.
# The archive only carries `python3.11 3.11.0~rc1-1~22.04.1` — a RELEASE
# CANDIDATE. `apt-get install python3.11` therefore succeeds but gives you
# an RC build, and `python3.11-venv` is missing from the base image on
# some architectures.
#
# The deadsnakes PPA carries proper 3.11 release builds for jammy.
#
# If you would rather not use a PPA, the supported alternative is to build
# the apps on Ubuntu 24.04 (which ships python3.12 natively) or to run the
# Python API in a container (add it to docker/docker-compose.yml).
set -euo pipefail

PYTHON_VERSION="${PYTHON_VERSION:-3.11}"

if [[ $EUID -ne 0 ]]; then
  echo "run as root" >&2
  exit 1
fi

apt-get update
apt-get -y install software-properties-common

add-apt-repository -y ppa:deadsnakes/ppa
apt-get update

apt-get -y install \
  "python${PYTHON_VERSION}" \
  "python${PYTHON_VERSION}-venv" \
  "python${PYTHON_VERSION}-dev" \
  "python${PYTHON_VERSION}-distutils"

actual="$(python${PYTHON_VERSION} --version 2>&1 || true)"
echo "[python] installed $actual"

# An RC build is not acceptable for production.
if echo "$actual" | grep -qiE "rc[0-9]"; then
  echo "[python] ERROR: '$actual' is a release candidate, not a stable release." >&2
  echo "           The deadsnakes PPA failed to provide a stable build." >&2
  exit 1
fi

# Verify the venv module is actually present.
if ! python${PYTHON_VERSION} -c "import venv, ensurepip" 2>/dev/null; then
  echo "[python] ERROR: the venv/ensurepip modules are missing." >&2
  exit 1
fi

echo "[python] ok"
