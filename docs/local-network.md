# When the local network keeps breaking

Some home networks lose the way between two computers for half a minute at a
time. Both computers are on and have the internet, but they do not reach each
other. An open folder hangs and is then cut off, and a file manager that tries
to open one says something like *Unable to access location*.

## What HomeMesh does about it

Nothing needs to be set up, but both computers need a VPN (*Away from home* in
the window sets up Tailscale).

- **At once:** a connection that finds no way on the local network goes through
  the VPN.
- **After the second break** in three days SSH tries the VPN first, so nothing
  has to fail first. The VPN's packets stay at home, and when the local network
  breaks, it finds another way through the router within seconds: open folders
  stay open. The window says so, and `homemesh status` does.
- **After three days without a break** it goes back to the local network.

A break is counted when a computer that answered on the local network answers
only through the VPN and then, within a quarter of an hour, locally again. The
two computers tell each other, as often only one of them can notice: the break
may be in one direction only. Nothing is measured and nothing is sent for that
beyond the packets that check who is on.

Measured on one such network (Wi-Fi 6, two laptops, a three-band mesh router),
with a connection open and idle, when one laptop's Wi-Fi moved to another band
(twice):

| | through the local network | through Tailscale |
| --- | --- | --- |
| the open connection | once cut off after 46 s, once silent for 55 s | nothing lost, silent for 7 and 4 s |
| speed of one SSH connection | 175 to 234 Mb/s | 156 to 250 Mb/s |
| processor, of one core | 8 to 14 % | 22 to 34 % |

## Where it comes from

Usually from the router: a computer moves from one Wi-Fi band to another, and
the router goes on sending what other computers on the first band have for it
there. It is worse when a computer keeps moving between bands although it
stays in one place.

Whether a computer does that (look at hours when it was not busy: with steady
traffic the moving can stop):

    journalctl -b -t wpa_supplicant | grep -c 'Associated with'

More than a few in an hour without carrying the computer around is too much.

## What helps

1. **The router's software (firmware)**, also on mesh nodes: update it, then
   restart them.
2. **A name of its own for each Wi-Fi band** (2.4 GHz, 5 GHz, the second
   5 GHz; on ASUS routers with *Smart Connect* off), with the computers on one
   of them. They then no longer move between
   the bands of one router, and still move between mesh nodes, as a band has
   the same name on all of them.
3. **A cable** for a computer that stays in one place.
4. **On a Linux computer with NetworkManager and wpa_supplicant:** as soon as
   a network has more than one access point, and the bands of one router are
   that, NetworkManager has wpa_supplicant look for a better one every 30
   seconds whenever the signal is below −65 dBm. That is a normal signal one
   room away. The number is fixed in NetworkManager, but can be changed on the
   running wpa_supplicant without disconnecting:

       wpa_cli -i wlan0 set_network 0 bgscan '"simple:30:-75:86400"'

   Below −75 dBm, where the access point really gets out of reach, it still
   looks around at once. To keep it, put this into
   `/etc/NetworkManager/dispatcher.d/60-wifi-roaming` (owned by root, mode 755):

       #!/bin/bash
       [ "$2" = up ] && [ -e "/sys/class/net/$1/phy80211" ] || exit 0
       cli() { wpa_cli -p /run/wpa_supplicant -i "$1" "${@:2}" 2>/dev/null; }
       id=$(cli "$1" status | sed -n 's/^id=//p')
       [ -n "$id" ] && [ "$(cli "$1" get_network "$id" bgscan)" = '"simple:30:-65:300"' ] &&
           cli "$1" set_network "$id" bgscan '"simple:30:-75:86400"'

   A weaker threshold (−70) suits a place where one node is still heard but
   another one is much closer. HomeMesh does not do this by itself: it would
   change how Wi-Fi works for every program and every network.
