#!/usr/bin/env python3
"""Prepare the checked-out kiosk app before its production build.

Moves the status-bar version label ahead of the other controls, adds the
app keyboard to the staff login page, then writes the published kiosk
bundle's public client variables to `.env.production`. Stdout lists
variable names only.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

VERSION_SPAN = '<span class="kiosk-status-bar__version">{{ releaseLabel }}</span>'
ACTIONS_OPEN = '<div class="kiosk-status-bar__actions row items-center">'

# Same published UI earlier images downloaded. Values are read from its
# compiled bundle and are not stored in this repository.
PUBLISHED_KIOSK_ORIGIN = "https://a26-tbw-root-kiosk-app-main.srvnve01.cleverativity.com"

# The dispenser picker blocks when either of these is empty. The public client
# key may be either name; the bundle supplies whichever the published app used.
REQUIRED_PUBLIC_ENV = (
    "VITE_SUPABASE_URL",
    "VITE_TBW_KIOSK_TENANT_ID",
)
PUBLIC_CLIENT_KEY_NAMES = (
    "VITE_SUPABASE_PUBLISHABLE_KEY",
    "VITE_SUPABASE_ANON_KEY",
)

_ENV_MARKER = "VITE_SUPABASE_URL:"
_MAX_SCRIPTS = 40


def move_version_first(text: str) -> str:
    if text.count(VERSION_SPAN) != 1:
        raise SystemExit("kiosk status bar version label was not found once")
    if text.count(ACTIONS_OPEN) != 1:
        raise SystemExit("kiosk status bar actions group was not found once")
    without = text.replace(VERSION_SPAN, "", 1)
    without = without.replace("\n\n\n", "\n\n", 1)
    insert_at = without.index(ACTIONS_OPEN) + len(ACTIONS_OPEN)
    return without[:insert_at] + "\n      " + VERSION_SPAN + without[insert_at:]


def parse_published_env(source: str) -> dict[str, str]:
    """Return VITE_ string values from the compiled `import.meta.env` object."""
    marker = source.find(_ENV_MARKER)
    if marker < 0:
        raise SystemExit("published kiosk bundle has no public client env object")
    brace = source.rfind("{", 0, marker)
    if brace < 0:
        raise SystemExit("published kiosk bundle has no public client env object")
    depth = 0
    end = None
    for index, char in enumerate(source[brace:], brace):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    if end is None:
        raise SystemExit("published kiosk bundle has no public client env object")
    obj = source[brace : end + 1]
    values: dict[str, str] = {}
    cursor = 1
    while cursor < len(obj) - 1:
        matched = re.match(r"\s*([A-Za-z0-9_]+)\s*:", obj[cursor:])
        if not matched:
            break
        key = matched.group(1)
        cursor += matched.end()
        if cursor >= len(obj) or obj[cursor] not in "`\"'":
            rest = re.match(r"[^,}]+", obj[cursor:])
            if rest is None:
                break
            cursor += rest.end()
            if obj[cursor : cursor + 1] == ",":
                cursor += 1
            continue
        quote = obj[cursor]
        cursor += 1
        chars: list[str] = []
        while cursor < len(obj) and obj[cursor] != quote:
            if obj[cursor] == "\\":
                if cursor + 1 >= len(obj):
                    raise SystemExit(f"{key} value is not a single line")
                chars.append(obj[cursor + 1])
                cursor += 2
                continue
            chars.append(obj[cursor])
            cursor += 1
        if cursor >= len(obj) or obj[cursor] != quote:
            raise SystemExit(f"{key} value is not a single line")
        cursor += 1
        if obj[cursor : cursor + 1] == ",":
            cursor += 1
        if key.startswith("VITE_"):
            values[key] = "".join(chars)
    if _ENV_MARKER[:-1] not in values and "VITE_SUPABASE_URL" not in values:
        raise SystemExit("published kiosk bundle has no public client env object")
    return values


def missing_required(values: dict[str, str]) -> list[str]:
    missing = [name for name in REQUIRED_PUBLIC_ENV if not values.get(name, "").strip()]
    if not any(values.get(name, "").strip() for name in PUBLIC_CLIENT_KEY_NAMES):
        missing.extend(PUBLIC_CLIENT_KEY_NAMES)
    return missing


def public_client_assignments(values: dict[str, str]) -> list[tuple[str, str]]:
    missing = missing_required(values)
    if missing:
        raise SystemExit(
            "published kiosk bundle is missing required public env: " + ", ".join(missing)
        )
    assignments: list[tuple[str, str]] = []
    for name in sorted(values):
        if not name.startswith("VITE_"):
            continue
        value = values[name]
        if not value.strip():
            continue
        if any(char in value for char in "\n\r\"'\\"):
            raise SystemExit(f"{name} value is not a single line")
        assignments.append((name, value))
    return assignments


def render_env_production(assignments: list[tuple[str, str]]) -> str:
    lines = [f'{name}="{value}"' for name, value in assignments]
    return "\n".join(lines) + "\n"


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "root-kiosk-image-build"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def load_published_env(origin: str = PUBLISHED_KIOSK_ORIGIN) -> dict[str, str]:
    origin = origin.rstrip("/")
    index = _fetch(origin + "/")
    queue = re.findall(r'(?:src|href)="(/assets/[^"]+\.js)"', index)
    seen: set[str] = set()
    while queue and len(seen) < _MAX_SCRIPTS:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        script = _fetch(origin + path)
        if _ENV_MARKER in script:
            return parse_published_env(script)
        for name in re.findall(r"assets/([A-Za-z0-9_.-]+\.js)", script):
            nxt = "/assets/" + name
            if nxt not in seen:
                queue.append(nxt)
    raise SystemExit("published kiosk bundle has no public client env object")


def write_production_env(root: Path, values: dict[str, str]) -> list[str]:
    assignments = public_client_assignments(values)
    path = root / ".env.production"
    path.write_text(render_env_production(assignments), encoding="utf-8")
    path.chmod(0o600)
    return [name for name, _value in assignments]


_LOGIN_FIELDS = """      <PulseTextField
        v-model="email"
        :label="t('login.email')"
        type="email"
        :input-props="{ autofocus: true, autocomplete: 'username' }"
      />
      <PulseTextField
        v-model="password"
        :label="t('login.password')"
        type="password"
        :input-props="{ autocomplete: 'current-password' }"
      />"""

_LOGIN_FIELDS_WITH_KEYBOARD = """      <div @focusin="activeCredentialField = 'email'">
        <PulseTextField
          v-model="email"
          :label="t('login.email')"
          type="email"
          :input-props="{ autofocus: true, autocomplete: 'username' }"
        />
      </div>
      <div @focusin="activeCredentialField = 'password'">
        <PulseTextField
          v-model="password"
          :label="t('login.password')"
          type="password"
          :input-props="{ autocomplete: 'current-password' }"
        />
      </div>
      <KioskQwertyKeyboard
        v-model="credentialValue"
        variant="credentials"
        class="kiosk-login-keyboard"
      />"""

_LOGIN_IMPORT = "import { TBW_KIOSK_HOME_PATH } from 'src/config/tbwKiosk';"
_LOGIN_IMPORT_WITH_KEYBOARD = (
    _LOGIN_IMPORT
    + "\nimport KioskQwertyKeyboard from 'src/components/kiosk/KioskQwertyKeyboard.vue';"
)

_LOGIN_STATE = "const oauthLoading = ref<'google' | 'azure' | null>(null);"
_LOGIN_STATE_WITH_KEYBOARD = """const oauthLoading = ref<'google' | 'azure' | null>(null);
type CredentialField = 'email' | 'password';
const activeCredentialField = ref<CredentialField>('email');
const credentialValue = computed({
  get(): string {
    return activeCredentialField.value === 'email' ? email.value : password.value;
  },
  set(value: string): void {
    if (activeCredentialField.value === 'email') {
      email.value = value;
    } else {
      password.value = value;
    }
  },
});"""

_LOGIN_STYLE = """
<style scoped>
.kiosk-login-keyboard {
  --kiosk-surface-lowest: #fff;
  --kiosk-on-surface: #1c1c1c;
  --kiosk-primary: #7f9b58;
  --kiosk-key-font: 1rem;
  margin-top: 0.75rem;
  width: 100%;
}
</style>
"""


def add_login_keyboard(text: str) -> str:
    """Show the app credentials keyboard on the staff login page."""
    if "kiosk-login-keyboard" in text:
        return text
    if text.count(_LOGIN_FIELDS) != 1:
        raise SystemExit("staff login page has no email and password fields")
    if text.count(_LOGIN_IMPORT) != 1 or text.count(_LOGIN_STATE) != 1:
        raise SystemExit("staff login page script anchors were not found")
    updated = text.replace(_LOGIN_FIELDS, _LOGIN_FIELDS_WITH_KEYBOARD, 1)
    updated = updated.replace(_LOGIN_IMPORT, _LOGIN_IMPORT_WITH_KEYBOARD, 1)
    updated = updated.replace(_LOGIN_STATE, _LOGIN_STATE_WITH_KEYBOARD, 1)
    if "</script>" not in updated:
        raise SystemExit("staff login page has no script block")
    if "kiosk-login-keyboard {" not in updated:
        updated = updated.replace("</script>", "</script>\n" + _LOGIN_STYLE, 1)
    if updated.count('variant="credentials"') != 1:
        raise SystemExit("staff login keyboard was not added")
    return updated


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    vue = root / "apps/tbw-root-kiosk-app/src/components/kiosk/KioskStatusBar.vue"
    updated = move_version_first(vue.read_text(encoding="utf-8"))
    if updated.index(VERSION_SPAN) > updated.index("onChangeDispenser"):
        raise SystemExit("version label is still after Change dispenser")
    vue.write_text(updated, encoding="utf-8")
    login = root / "apps/tbw-root-kiosk-app/src/pages/LoginPage.vue"
    login.write_text(add_login_keyboard(login.read_text(encoding="utf-8")), encoding="utf-8")
    print("staff login keyboard: app credentials keyboard")
    names = write_production_env(root, load_published_env())
    print("public client env: " + ", ".join(names))


if __name__ == "__main__":
    main()
