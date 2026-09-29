Cleverativity kiosk — config partition
=====================================

After flashing, this FAT32 partition can be edited from any computer.

kioskbrowser.ini
  Homepage, WiFi, keyboard layout, VNC, timezone.
  The default homepage is the on-device kiosk UI (http://127.0.0.1/).

splash.png
  Boot / Openbox splash screen.

openvpn.ovpn
  OpenVPN client profile. The tunnel must come up for SSH and VNC.

authorized_keys
  SSH public keys allowed to log in as pi or root. Only accepted from the VPN.

kiosk_admin / kiosk_admin.pub
  Pre-generated admin key. Copy kiosk_admin to your PC, then after VPN is up:

    ssh -i kiosk_admin pi@<kiosk-vpn-ip>

WiFi defaults: CleverWiFi and CleverWiFiX (same PSK).
Keyboard: Italian (it). Screen never blanks. On-screen keyboard is shown.
