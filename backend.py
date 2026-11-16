#!/usr/bin/env python3
"""VPN Switch backend: saved Cisco AnyConnect profiles, keyring passwords, NetworkManager tunnels."""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import gi

gi.require_version("NM", "1.0")
from gi.repository import Gio, GLib, NM

SERVICE = "org.freedesktop.NetworkManager.openconnect"
KEYRING_SERVICE = "omarchy-vpn-switch"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy" / "vpn-switch" / "profiles.json"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
UUID = re.compile(r"^[0-9a-fA-F-]{36}$")


class VpnError(Exception):
    pass


def run(args, *, input=None, timeout=100):
    try:
        done = subprocess.run(args, input=input, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise VpnError(f"{args[0]} timed out") from error
    except OSError as error:
        raise VpnError(f"Could not start {args[0]}: {error.strerror}") from error
    return done


def nmcli(*args):
    done = run(["nmcli", *args])
    if done.returncode:
        raise VpnError(f"NetworkManager command failed: {done.stderr.strip()}")
    return done.stdout.strip()


# --- stored profiles (no secrets) -------------------------------------------

def load():
    try:
        data = json.loads(CONFIG.read_text())
    except FileNotFoundError:
        return []
    except ValueError as error:
        raise VpnError("profiles.json is corrupt") from error
    return [p for p in data if isinstance(p, dict) and NAME.fullmatch(p.get("name", ""))]


def save(profiles):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(profiles, indent=2))
    tmp.chmod(0o600)
    tmp.replace(CONFIG)


def find(name):
    for profile in load():
        if profile["name"] == name:
            return profile
    raise VpnError(f"No saved VPN named {name}")


# --- keyring ----------------------------------------------------------------

def get_password(name):
    done = run(["secret-tool", "lookup", "service", KEYRING_SERVICE, "profile", name], timeout=30)
    return done.stdout.rstrip("\n") if done.returncode == 0 else ""


def set_password(name, password):
    done = run(["secret-tool", "store", f"--label=VPN Switch: {name}",
                "service", KEYRING_SERVICE, "profile", name], input=password, timeout=30)
    if done.returncode:
        raise VpnError("Could not store the password in the keyring")


def clear_password(name):
    run(["secret-tool", "clear", "service", KEYRING_SERVICE, "profile", name], timeout=30)


# --- NetworkManager ---------------------------------------------------------

def nm_name(name):
    return f"VPN Switch - {name}"


def nm_uuid(name):
    rows = nmcli("-t", "--escape", "no", "-f", "UUID,TYPE,NAME", "connection", "show")
    for line in rows.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[2] == nm_name(name) and parts[1] == "vpn" and UUID.fullmatch(parts[0]):
            return parts[0]
    return ""


def active_uuids():
    return set(nmcli("-t", "-f", "UUID", "connection", "show", "--active").splitlines())


def server_url(value):
    value = value.strip()
    if not value or "," in value or any(c.isspace() for c in value):
        raise VpnError("Enter a VPN server address without spaces or commas")
    address = value if "://" in value else "https://" + value
    parsed = urlsplit(address)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise VpnError("Enter a valid HTTPS VPN server address")
    try:
        parsed.port
    except ValueError as error:
        raise VpnError("Invalid VPN server port") from error
    return address


def ensure_nm_profile(name, address, username):
    data = f"gateway={address},protocol=anyconnect"
    uuid = nm_uuid(name)
    if uuid:
        nmcli("connection", "modify", "uuid", uuid, "vpn.data", data,
              "vpn.user-name", username, "connection.autoconnect", "no")
    else:
        nmcli("connection", "add", "type", "vpn", "vpn-type", "openconnect", "ifname", "*",
              "con-name", nm_name(name), "autoconnect", "no", "--",
              "vpn.data", data, "vpn.user-name", username)
        uuid = nm_uuid(name)
        if not uuid:
            raise VpnError("NetworkManager did not create the VPN profile")
    return uuid


def authentication_error(stderr):
    text = stderr.lower()
    if any(t in text for t in ("certificate", "servercert", "issuer", "trust anchor")):
        return "VPN server certificate could not be verified; check with your IT team"
    if any(t in text for t in ("resolve", "getaddrinfo", "name or service not known")):
        return "VPN server name could not be resolved"
    if any(t in text for t in ("failed to connect", "connection refused", "network is unreachable")):
        return "Could not reach the VPN server; check the address and network"
    if any(t in text for t in ("authgroup", "non-interactive", "form entry", "token", "challenge")):
        return "This VPN needs an extra sign-in step (group, token or SSO) that VPN Switch does not support"
    if any(t in text for t in ("login failed", "authentication failed", "invalid credentials", "incorrect password")):
        return "VPN rejected the credentials; update the password and try again"
    return "OpenConnect authentication failed; the gateway may require another login step"


def authenticate(address, username, password):
    done = run(["openconnect", "--protocol=anyconnect", "--authenticate", "--non-inter",
                "--passwd-on-stdin", "--user", username, address], input=password + "\n", timeout=90)
    if done.returncode:
        raise VpnError(authentication_error(done.stderr))
    values = {}
    for line in done.stdout.splitlines():
        key, sep, raw = line.partition("=")
        if not sep or key not in ("COOKIE", "CONNECT_URL", "FINGERPRINT", "RESOLVE"):
            continue
        try:
            parts = shlex.split(raw)
        except ValueError as error:
            raise VpnError("OpenConnect returned malformed authentication data") from error
        if len(parts) != 1:
            raise VpnError("OpenConnect returned malformed authentication data")
        values[key] = parts[0]
    if not all(values.get(k) for k in ("COOKIE", "CONNECT_URL", "FINGERPRINT")):
        raise VpnError("OpenConnect did not return the gateway, cookie and certificate fingerprint")
    return values


def activate(uuid, values):
    secrets = {"cookie": values["COOKIE"], "gateway": values["CONNECT_URL"], "gwcert": values["FINGERPRINT"]}
    if values.get("RESOLVE"):
        secrets["resolve"] = values["RESOLVE"]
    client = NM.Client.new(None)  # must outlive `connection`
    connection = client.get_connection_by_uuid(uuid)
    vpn = connection.get_setting_vpn() if connection else None
    if vpn is None:
        raise VpnError("NetworkManager could not find the VPN profile")
    for key, value in secrets.items():
        vpn.add_secret(key, value)
    try:
        if not connection.commit_changes(False, None):
            raise VpnError("Could not pass VPN credentials to NetworkManager")
        done = run(["nmcli", "--wait", "90", "connection", "up", "uuid", uuid])
        if done.returncode:
            raise VpnError(f"VPN activation failed (NetworkManager exit code {done.returncode})")
    finally:
        Gio.bus_get_sync(Gio.BusType.SYSTEM, None).call_sync(
            "org.freedesktop.NetworkManager", connection.get_path(),
            "org.freedesktop.NetworkManager.Settings.Connection", "ClearSecrets",
            None, None, Gio.DBusCallFlags.NONE, 10000, None)


# --- actions ----------------------------------------------------------------

def listing():
    active = active_uuids()
    out = []
    for p in load():
        uuid = nm_uuid(p["name"])
        out.append({"name": p["name"], "server": p["server"], "username": p["username"],
                    "connected": bool(uuid) and uuid in active,
                    "hasPassword": bool(get_password(p["name"]))})
    return {"profiles": out}


def add(request):
    name = request.get("name", "").strip()
    username = request.get("username", "").strip()
    password = request.get("password", "")
    if not NAME.fullmatch(name):
        raise VpnError("Name: 1-40 letters, digits, spaces, dots, dashes or underscores")
    address = server_url(request.get("server", ""))
    if not username or any(c in username + password for c in "\r\n"):
        raise VpnError("Enter a username")
    if password:
        set_password(name, password)
    elif not get_password(name):
        raise VpnError("Enter a password")
    profiles = [p for p in load() if p["name"] != name]
    profiles.append({"name": name, "server": address, "username": username})
    save(profiles)
    ensure_nm_profile(name, address, username)


def remove(name):
    find(name)
    uuid = nm_uuid(name)
    if uuid:
        if uuid in active_uuids():
            nmcli("connection", "down", "uuid", uuid)
        nmcli("connection", "delete", "uuid", uuid)
    clear_password(name)
    save([p for p in load() if p["name"] != name])


def connect(name):
    profile = find(name)
    password = get_password(name)
    if not password:
        raise VpnError("No saved password; edit this VPN and enter it again")
    # Only one Cisco tunnel at a time: routes and DNS of two gateways would fight.
    for other in load():
        other_uuid = nm_uuid(other["name"])
        if other["name"] != name and other_uuid in active_uuids():
            nmcli("connection", "down", "uuid", other_uuid)
    uuid = ensure_nm_profile(name, profile["server"], profile["username"])
    if uuid in active_uuids():
        return
    activate(uuid, authenticate(profile["server"], profile["username"], password))


def disconnect(name):
    find(name)
    uuid = nm_uuid(name)
    if uuid and uuid in active_uuids():
        nmcli("connection", "down", "uuid", uuid)


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if action == "add":
        add(json.loads(sys.stdin.readline()))
    elif action == "remove":
        remove(arg)
    elif action == "connect":
        connect(arg)
    elif action == "disconnect":
        disconnect(arg)
    elif action != "list":
        raise VpnError("Expected list, add, remove, connect or disconnect")
    return listing()


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, **main()}))
    except GLib.Error:
        print(json.dumps({"ok": False, "error": "NetworkManager rejected the VPN operation"}))
        sys.exit(1)
    except (VpnError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        sys.exit(1)
