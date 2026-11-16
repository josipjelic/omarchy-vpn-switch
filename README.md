# Omarchy VPN Switch

A bar widget for [Omarchy](https://omarchy.org/) that keeps several Cisco
AnyConnect-compatible VPNs and lets you flip each one on or off with a switch.
Passwords are remembered in your keyring, so there is nothing to type after the
first setup.

OpenConnect authenticates to the gateway, NetworkManager manages the tunnel,
and a Quickshell popup lists your VPNs with Omarchy's own square toggle switch.

## Install

```sh
omarchy plugin add https://github.com/josipjelic/omarchy-vpn-switch.git --enable
```

Requirements (preinstalled on most Omarchy systems):

```sh
omarchy pkg add networkmanager-openconnect python-gobject libsecret
```

`libsecret` provides `secret-tool`, and a running keyring such as
`gnome-keyring` is needed to store passwords.

## Use

1. Click the VPN icon in the bar.
2. Press **Add VPN** and enter a name, server (hostname or HTTPS URL),
   username and password.
3. Flip the switch to connect or disconnect.

Click a VPN's name to edit it. Leave the password blank to keep the saved one,
or press **Remove** to delete the VPN, its NetworkManager profile and its
keyring entry. Only one VPN Switch tunnel is active at a time; turning one on
turns the other off.

## Remove

Turn off active VPNs, then:

```sh
omarchy plugin remove josipjelic.vpn-switch
```

This does not delete your saved VPNs. To clean up fully, press **Remove** on
each VPN before uninstalling, or delete `~/.config/omarchy/vpn-switch/`,
the `VPN Switch - *` NetworkManager profiles
(`nmcli connection delete "VPN Switch - <name>"`) and the keyring entries
(`secret-tool clear service omarchy-vpn-switch profile <name>`).

## Where data lives

| What | Where |
| --- | --- |
| Name, server, username | `~/.config/omarchy/vpn-switch/profiles.json` (mode 0600) |
| Password | Secret Service keyring, attributes `service=omarchy-vpn-switch`, `profile=<name>` |
| Tunnel | NetworkManager profile `VPN Switch - <name>`, autoconnect off |

The password is sent over stdin to `openconnect --authenticate` for each
connection and never passed as a command argument. The short-lived session
cookie is handed to NetworkManager in memory and cleared afterwards.
Certificate validation is never disabled.

## Limitations

Only Cisco AnyConnect username and password logins are supported. Groups,
OTP/MFA prompts, browser SSO and client certificates are not.

## Development

```sh
omarchy plugin validate .
python backend.py list
```

Changes to an installed plugin hot-reload the shell. If a QML edit does not
apply, run `omarchy restart shell`.

Licensed under [MIT](LICENSE).
