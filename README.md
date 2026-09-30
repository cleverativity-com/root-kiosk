root-kiosk (Cleverativity image)
=============================

This repository builds a **Cleverativity kiosk image** (root-kiosk).

Defaults baked into `/boot/firmware`:

- Homepage: the on-device TBW kiosk UI at `http://127.0.0.1/`
- Hardware API: `tbw-root-api` on `127.0.0.1:8765`, reached by the UI through nginx at `/api/v1`
- Splash screen: Cleverativity logo (`splash.png`)
- Keyboard layout: Italian (`it`) plus an on-screen keyboard (onboard)
- Screen never blanks
- WiFi: `CleverWiFi` and `CleverWiFiX`
- OpenVPN client (`openvpn.ovpn`); SSH (22) and VNC (5900) are firewalled so they are **only reachable from the VPN**
- SSH admin key on the FAT32 partition: `kiosk_admin` (see `authorized_keys`)

Flash the image, then optionally edit `kioskbrowser.ini` on the boot partition. After the VPN comes up:

```
ssh -i kiosk_admin pi@<kiosk-vpn-ip>
```

VNC: connect to `<kiosk-vpn-ip>:5900` (no password; access is limited to the tunnel).

---

root-kiosk
=============================

<img src="https://screenshot.tbspace.de/zachejgwlkq.jpg" width="45%"> <img src="https://screenshot.tbspace.de/kuhmlynagbw.jpg" width="45%">
<img src="https://screenshot.tbspace.de/tdouafprbqk.jpg" width="45%"> <img src="https://screenshot.tbspace.de/rmhezfgucdj.jpg" width="45%">

## Overview
Another kiosk browser OS? Yes, this one is a little bit opinionated :grin:  

This project is a Debian Linux-based OS for computers, either PCs or Raspberry Pi's and has only one job:  
| :computer:  Display a web page in full screen very reliably and securely   |
|----------------------------------------------|

The author of this project ran several similar setups in production for years and has seen a lot of problems and strange failure modes.  
This project aims to solve a lot of those (at least for the author), it might also be useful for others :)  

Other similar projects:
- will run the computer in 32bit mode (making them very slow/laggy)
- write to the storage device (killing it in the long run, causing bad reliability)
- have insecure configurations (like open ports, network access or unsafe UI features)
- are not built via CI (instead have people manually building images)
- are missing watchdog functionality (can hang on browser error pages forever)

## Key features
- [Images built via CI](https://github.com/cleverativity/root-kiosk/blob/main/.github/workflows/main.yml) and published to [Releases](https://github.com/cleverativity/root-kiosk/releases) on every push to `main`
- WiFi & Ethernet connection support
- Raspberry Pi 5 (64-bit)
- [USB flash drive, USB SSD, etc. compatible](#how-to--installation-guide)
- aarch64 image for Raspberry Pi 5
- Read-only filesystem (no more broken SD cards)
- Browser cache can be cleared at configurable intervals
- [HTTP watchdog (website needs to send heartbeat messages via XHR/AJAX to localhost)](#http-watchdog-functionality)
- Force specific resolution (1080p on 4k screens, broken EDID, etc.)
- Configurable audio output (HDMI, 3.5mm, USB audio, etc.)
- Hard NTP handling (will wait for NTP at boot)
- SSH support
- VNC support
- SSH tunneling support (for remote-access without port-forwarding, on DS-Lite/cellular connections, etc.)
- Graphical splash screen while booting
- Support for multi-touch touch-screens and gestures
- Dark mode support
- Configurable timezone and keyboard layout
- Optional screen blanking (DPMS) interval for interactive systems
- [Local webserver with PHP support](#local-webserver) (can host simple HTML, landing pages, slideshows, iFrame mechanisms, etc.)

## Supported platforms
- Raspberry Pi 5: use `root-kiosk-*-arm64-raspberrypi.img.xz`

## Application examples
- Digital signage
- Video streams (Cameras, Livestreams, etc.)
- Grafana dashboard
- Public transport timetable
- Digital picture frame/slideshow
- Victron Solar dashboard
- Interactive maps
- Digital concierge
- Magic mirrors

> [!TIP]
> Combining root-kiosk with an existing web CMS (like Typo3) is an excellent way to build a very flexible digital signage solution:  
> By configuring a hidden/special sub-page with a full-screen layout, employees can easily modify the digital signage solution themselves.
> Often teams are already trained on the existing content management systems, reducing training times.  
> It will also work without any monthly fees (unlike other hosted/SaaS/cloud-based digital signage solutions).

## Planned features:
- Raspberry Pi PXE/network boot support
- Network connectivity watchdog (configurable ping, etc. timeout)
- Automatic reboot at specified time

## Security considerations:
- Autossh does not check SSH host keys. This is okay-ish as long as the target server only allows tunneling, nothing else.
- nginx/PHP are allowed to use sudo/NOPASSWD (because it needs to query the VideoCore, manage service, etc.), more priviledge seperation would be nice
- due to the skeleton mechanism, the system has some ... creative permissions. some cleanup required.
- root-kiosk is not built in a reproducible/repeatible way. This is basically unfixable due to the nature of the build process.

## How-To / Installation guide

> [!IMPORTANT]  
> root-kiosk does not have an installer for x86 PCs. On PCs, you'll need to write the image to the storage somehow.
> Either write the storage media (like NVMe or SATA storage) externally using another PC or boot a Linux Live-ISO and use dd to flash the image.

Just like any other Raspberry Pi image:   
Download the current .img.xz file from the [Releases](https://github.com/cleverativity/root-kiosk/releases) page and flash it to a storage device of your choice.  
SD cards, USB flash drives, USB SSDs, SATA SSDs, NVMe SSDs are all good options.  
You can use a tool like the [Raspberry Pi Imager](https://www.raspberrypi.com/software/), [BalenaEtcher](https://etcher.balena.io/), [Win32DiskImager](https://sourceforge.net/projects/win32diskimager/) or plain "dd" on \*nix-like systems.   
When using the latter two, make sure to extract the .gz compression first (using a tool like 7zip).  

After flashing, re-plug the storage device and open the FAT32 partition.  
Open the [`kioskbrowser.ini`](https://github.com/cleverativity/root-kiosk/blob/main/kiosk_skeleton/boot/firmware/kioskbrowser.ini) file in a text editor and change everything to your needs.  
More complex WiFi setups (like WPA2-Enterprise) can be configured by creating a wpa_supplicant.conf.  
Adding your own SSH keys can be done by creating a authorized_keys file.  
If you want to use the autossh tunneling features, copy an SSH private key as either "id_rsa" or "id_ed25519".  
The splash screen can be customized by replacing (or entirely removing) `splash.png`.

## HTTP watchdog functionality
Browsers are complex, networks are unstable and software can be buggy.   
In order to get the highest reliability possible, self-hosted websites can be modified to include a heartbeat/watchdog functionality.
This works by requesting a certain http-endpoint from the website at some interval.   
If your page is being reloaded often (like with a <meta refresh=-header), you can just load the heartbeat-URL as an image:
```html
<img src="http://localhost/heartbeat.php" style="display: none;">
```

If your page stays on one page for a long time (or is just a single-page application), you might want to use AJAX requests to send a heartbeat:
```html
<script>
const req = new XMLHttpRequest();
setInterval(function() {
	req.open("GET", "http://localhost/heartbeat.php");
	req.send();
}, 2000);
</script>
```

Whenever the heartbeat stops (for whatever reason), the device will first restart the X11 environment (browser, window manager, etc.) and later (if it hasn't recovered) the whole system by rebooting.

## TBW kiosk app and API

Chromium opens the TBW kiosk UI (`apps/tbw-root-kiosk-app`) from nginx on this machine. nginx serves that UI from `/var/www/html` and proxies `/api/` plus `/health` to the `tbw-root-api` systemd service, which listens on `127.0.0.1:8765` only.

The image build downloads the published UI from `https://a26-tbw-root-kiosk-app-main.srvnve01.cleverativity.com/`. To install the copies from [accleverate-v26](https://github.com/cleverativity-com/accleverate-v26) instead, set the GitHub Actions secret `ACCLEVERATE_READ_TOKEN` (read access to that repository). The workflow sparse-checkouts `apps/tbw-root-kiosk-app` and `services/tbw-root-api` into `third_party/accleverate-v26`, and `build.sh` installs them.

Without that checkout, the image runs the bundled API in `kiosk_skeleton/opt/tbw-root-api`. It implements the same `/api/v1` contract the kiosk UI calls and simulates the dispenser. It does not drive GPIO; the upstream service owns the pin map.

Heartbeat and screenshot PHP endpoints stay on nginx next to the kiosk UI.

## Local webserver
root-kiosk ships with an nginx webserver and a PHP runtime by default (which is used internally for the heartbeat mechanism).  
Users can create a folder called `www-public` on the FAT32 partition and put custom HTML or PHP scripts there.  
Any files placed in the `/boot/firmware/www-public` folder will be available via `http://localhost/www-public/`.  
Files called `index.php` will be served as the directory index.  

This allows for a number of different mechanisms to be self-hosted, even without any network connectivity at all!
- Local user interfaces (interaction with GPIOs, sensors, cameras, local storage, etc.)
- Offline slideshow (folder full of JPEGs)
- [Small iFrame application to switch between different pages on display](https://gist.github.com/Manawyrm/86f3d4a762fd5138a4ffa7ba4d180d24)
- Custom digital signage code to download/render online info

## Inspiration / Other Kiosk-OSes:
- https://github.com/jareware/chilipie-kiosk/
- https://github.com/guysoft/FullPageOS
