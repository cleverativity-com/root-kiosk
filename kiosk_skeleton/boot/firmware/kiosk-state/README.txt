This directory is the writable settings area for the kiosk UI.

The root filesystem stays read-only. kiosk-settings remounts this FAT
partition read-write only while it replaces one of these files, then
mounts it read-only again:

  network.json     Wi-Fi network and IPv4 settings (DHCP or static)
  dispenser.json   which dispenser this machine is

Do not power the machine off while a setting is being saved.
A hand-written network.json is applied at boot and overrides both
kioskbrowser.ini and wpa_supplicant.conf.
