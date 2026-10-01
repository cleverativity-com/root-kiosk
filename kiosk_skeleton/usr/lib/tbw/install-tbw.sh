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
	python3 -m venv /opt/tbw-root-api/venv
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
	return 0
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
	return 0
}

if [ -d "${SRC}" ]; then
	if [ -d "${SRC}/services/tbw-root-api" ]; then
		install_upstream_api
	fi
	if ! install_upstream_ui; then
		echo "Upstream kiosk app has no dist/ build; downloading the published UI"
		python3 /usr/lib/tbw/fetch-kiosk-app.py --url "${KIOSK_URL}" --dest "${WEB_ROOT}"
	fi
	rm -rf "${SRC}"
else
	python3 /usr/lib/tbw/fetch-kiosk-app.py --url "${KIOSK_URL}" --dest "${WEB_ROOT}"
fi

chown -R www-data:www-data "${WEB_ROOT}"
chown root:root /opt/tbw-root-api /usr/bin/tbw-root-api
chmod 755 /usr/bin/tbw-root-api
