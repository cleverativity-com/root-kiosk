#!/bin/bash
# Install the TBW kiosk UI into nginx and the local hardware API.
# When /opt/src/accleverate-v26 is present (copied in by the image build),
# that tree replaces the bundled API and, if it contains a built UI, the UI.
set -euo pipefail

KIOSK_URL="${TBW_KIOSK_APP_URL:-https://a26-tbw-root-kiosk-app-main.srvnve01.cleverativity.com/}"
SRC="${TBW_ACCLEVERATE_SRC:-/opt/src/accleverate-v26}"
WEB_ROOT=/var/www/html

if ! id tbw >/dev/null 2>&1; then
	useradd --system --user-group --home-dir /var/lib/tbw-root-api --shell /usr/sbin/nologin tbw
fi
if ! getent group gpio >/dev/null 2>&1; then
	groupadd --system gpio
fi
usermod -aG gpio tbw

mkdir -p /var/lib/tbw-root-api /etc/tbw-root-api
if ! grep -q '/var/lib/tbw-root-api' /etc/fstab; then
	tbw_id="$(id -u tbw)"
	echo "tmpfs		/var/lib/tbw-root-api	tmpfs	defaults,noatime,nosuid,uid=${tbw_id},gid=${tbw_id},mode=0750,size=30m    0 0" >> /etc/fstab
fi

install_upstream_api() {
	local api="${SRC}/services/tbw-root-api"
	if [ ! -d "${api}" ]; then
		return 1
	fi
	echo "Installing tbw-root-api from ${api}"
	rm -rf /opt/tbw-root-api/venv
	# A checkout often ships TBW_HARDWARE=sim for the cloud container.
	# Rewrite that before packaging so the installed tree pulses GPIO.
	python3 /usr/lib/tbw/gpio_env.py --normalize-tree "${api}"
	# system site packages so this venv can import python3-lgpio (required on Pi 5).
	python3 -m venv --system-site-packages /opt/tbw-root-api/venv
	if [ -f "${api}/pyproject.toml" ] || [ -f "${api}/setup.py" ]; then
		/opt/tbw-root-api/venv/bin/pip install --no-cache-dir "${api}"
	elif [ -f "${api}/requirements.txt" ]; then
		/opt/tbw-root-api/venv/bin/pip install --no-cache-dir -r "${api}/requirements.txt"
		if [ -d "${api}/tbw_root_api" ]; then
			rm -rf /opt/tbw-root-api/tbw_root_api
			cp -a "${api}/tbw_root_api" /opt/tbw-root-api/tbw_root_api
		fi
	elif [ -d "${api}/tbw_root_api" ]; then
		rm -rf /opt/tbw-root-api/tbw_root_api
		cp -a "${api}/tbw_root_api" /opt/tbw-root-api/tbw_root_api
		rm -rf /opt/tbw-root-api/venv
	else
		echo "Unrecognized tbw-root-api layout in ${api}" >&2
		return 1
	fi
	# The service entry point matches the published API package name.
	printf 'TBW_UVICORN_APP=%s\n' "tbw_root_api.main:app" > /etc/tbw-root-api/app.env
	# The venv does not see the system gpiozero package. The copy-only layout has no venv.
	if [ -x /opt/tbw-root-api/venv/bin/pip ]; then
		/opt/tbw-root-api/venv/bin/pip install --no-cache-dir gpiozero
	fi
	python3 /usr/lib/tbw/gpio_env.py --normalize-tree /opt/tbw-root-api
	return 0
}

append_repo_identity() {
	local pkg="${SRC}/apps/tbw-root-kiosk-app/package.json"
	if [ -f "${pkg}" ]; then
		python3 - "${pkg}" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
name = data.get("name") or ""
version = data.get("version")
if name:
    print(f"app_name={name}")
if isinstance(version, str) and version:
    print(f"app_version={version}")
PY
	fi
	if [ -d "${SRC}/.git" ] && command -v git >/dev/null 2>&1; then
		printf 'git_revision=%s\n' "$(git -C "${SRC}" rev-parse HEAD)"
		git -C "${SRC}" log -1 --format='git_subject=%s'
	fi
}

write_ui_source() {
	mkdir -p /etc/tbw-root-api
	{
		printf 'kiosk_ui=%s\n' "$1"
		printf 'recorded_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		append_repo_identity
	} > /etc/tbw-root-api/kiosk-ui-source
}

download_published_ui() {
	python3 /usr/lib/tbw/fetch-kiosk-app.py --url "${KIOSK_URL}" --dest "${WEB_ROOT}"
	local headers last_modified etag
	headers="$(curl -fsSI -A root-kiosk-image-build -L --max-redirs 3 "${KIOSK_URL}" || true)"
	last_modified="$(printf '%s\n' "${headers}" | awk -F': ' 'tolower($1)=="last-modified" { value=$2 } END { gsub(/\r/, "", value); print value }')"
	etag="$(printf '%s\n' "${headers}" | awk -F': ' 'tolower($1)=="etag" { value=$2 } END { gsub(/\r/, "", value); print value }')"
	{
		printf 'kiosk_ui=%s\n' "${KIOSK_URL}"
		printf 'last_modified=%s\n' "${last_modified}"
		printf 'etag=%s\n' "${etag}"
		printf 'recorded_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		if [ -d "${SRC}" ]; then
			append_repo_identity
		else
			printf '%s\n' "repo_checkout=not-present"
		fi
	} > /etc/tbw-root-api/kiosk-ui-source
}

install_upstream_ui() {
	local app="${SRC}/apps/tbw-root-kiosk-app"
	local dist=""
	if [ -f "${app}/dist/spa/index.html" ]; then
		dist="${app}/dist/spa"
	elif [ -f "${app}/dist/index.html" ]; then
		dist="${app}/dist"
	elif [ -f "${app}/package.json" ] && command -v npm >/dev/null 2>&1; then
		echo "Building tbw-root-kiosk-app"
		(cd "${app}" && npm ci && npm run build)
		if [ -f "${app}/dist/spa/index.html" ]; then
			dist="${app}/dist/spa"
		elif [ -f "${app}/dist/index.html" ]; then
			dist="${app}/dist"
		fi
	fi
	if [ -z "${dist}" ]; then
		return 1
	fi
	echo "Installing kiosk UI from ${dist}"
	cp -a "${dist}/." "${WEB_ROOT}/"
	write_ui_source "${dist}"
	return 0
}

if [ -d "${SRC}" ]; then
	if [ -d "${SRC}/services/tbw-root-api" ]; then
		install_upstream_api
	fi
	if ! install_upstream_ui; then
		echo "Upstream kiosk app has no dist/ build; downloading the published UI"
		download_published_ui
	fi
	rm -rf "${SRC}"
else
	download_published_ui
fi

# The kiosk .env is gpio even when the upstream package shipped sim.
python3 /usr/lib/tbw/gpio_env.py --write /etc/tbw-root-api/.env
if [ ! -f /etc/tbw-root-api/app.env ]; then
	printf 'TBW_UVICORN_APP=%s\n' "tbw_root_api.main:app" > /etc/tbw-root-api/app.env
fi
python3 /usr/lib/tbw/gpio_env.py --normalize /etc/tbw-root-api/app.env --ensure

chown -R www-data:www-data "${WEB_ROOT}"
chown root:root /opt/tbw-root-api /usr/bin/tbw-root-api
chmod 755 /usr/bin/tbw-root-api
