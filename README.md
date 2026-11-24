# HomeMesh

**English** · [Polski](README.pl.md)

The home folders of all your computers, right in your file manager. Pair two
computers once with a six-digit code, like Bluetooth devices, and each one
shows up in the sidebar of Nautilus, Dolphin, Thunar, Nemo or Caja: click it
and you are browsing, copying and editing files on the other computer. At
home over the local network; away from home through a VPN, if you have one.

No Samba, no NFS, no server to run: underneath is plain SSH/SFTP, which every
Linux computer already has.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/en/overview-dark.png">
  <img alt="The HomeMesh window: this computer with its sharing switch, what other computers see, access from away and login tasks; paired computers with their status; a computer nearby ready to pair" src="docs/screenshots/en/overview-light.png" width="544">
</picture>

## At a glance

| | |
|---|---|
| **Three clicks** | *Turn on* sharing, open HomeMesh on the other computer, click *Connect* and check that both screens show the same code. A bar at the top of the window always says what to do next and offers a fix when something is wrong. |
| **Paired like Bluetooth** | Computers swap SSH keys and compare a six-digit code. If the codes match, nobody on the network tampered with the exchange. No passwords, no typing addresses. |
| **Introductions** | Pair a new computer with any member of the group and it gets to know all of them; members it never met learn about it the next time they talk. Removing a computer spreads the same way. |
| **Your file manager** | Paired computers appear as bookmarks in GTK file managers (GVfs) and in Dolphin (KIO). A click mounts; optionally they mount at login. A computer that is off is marked: *(off) laptop*. |
| **Choose what to share** | The whole home folder, or only chosen folders, each read-write or read-only. Chosen folders are served from a sandbox: the rest of your home, your keys and the system are simply not there. |
| **Away from home** | One click sets up Tailscale: HomeMesh installs it, starts it and opens the login page; no commands. Already have NetBird, ZeroTier, Yggdrasil, Nebula or WireGuard? It finds them by itself. Each connection goes over the local network when possible and through the VPN otherwise, with the same bookmarks. |
| **Light** | GTK 4 without libadwaita, so it fits any desktop. No system daemon: sharing is the system's `sshd`; the window is needed only to find and pair computers, and one small process in your session marks the ones that are off. Works with or without systemd. |
| **Terminal too** | Everything the window does, `homemesh` does in a terminal, so headless machines can join. |

## Screenshots

<table>
<tr>
<td width="50%" valign="top">

**Pairing**: both computers show the same code.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/en/pair-dark.png">
  <img alt="Pairing window with the code 482 913 and buttons Does not match and Matches" src="docs/screenshots/en/pair-light.png">
</picture>

**What other computers see**: everything, or chosen folders.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/en/share-dark.png">
  <img alt="Sharing window: whole home folder or only chosen folders, with Public read-write and Music read-only" src="docs/screenshots/en/share-light.png">
</picture>

</td>
<td width="50%" valign="top">

**Away from home**: Tailscale set up with one click.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/en/away-dark.png">
  <img alt="Access from away window offering to set up Tailscale" src="docs/screenshots/en/away-light.png">
</picture>

</td>
</tr>
</table>

The interface speaks English and Polish; [translations](#translations) are welcome.

## Install

From the AUR, the latest release or the development version:

    paru -S homemesh          # or: yay -S homemesh
    paru -S homemesh-git

From a checkout:

    packaging/arch/build-local.sh
    sudo pacman -U build/arch/homemesh-*.pkg.tar.zst

Needs `openssh`, `python-gobject`, `gtk4` and `polkit`. Optional: `gvfs` for
GTK file managers, `kio-extras` for Dolphin, `bubblewrap` for sharing only
chosen folders, `avahi` and `nss-mdns` for `name.local` addresses on systems
without systemd-resolved. When something is missing, HomeMesh shows *Install
missing packages* and installs it with your distribution's package manager
(pacman, apt, dnf, zypper, xbps or apk); in a terminal it asks first, or
`homemesh enable --install`.

## Quick start

1. Open **HomeMesh** and click **Turn on**. The computer asks for the
   administrator password once.
2. Do the same on another computer on the same network. It appears under
   *Nearby*.
3. Click **Connect** and check that both screens show the same code; confirm
   on both.

The other computer is now in your file manager's sidebar. To add a third
computer, pair it with either of the two.

## Away from home

At home computers talk over the local network. Away from home they need a VPN
to find each other over the internet: two computers behind home routers
cannot connect without a helper with a public address, and the existing VPNs
do that job well. HomeMesh does not ship its own; it detects Tailscale,
NetBird, ZeroTier, Yggdrasil, Nebula and WireGuard on its own.

The simplest is Tailscale (free for personal use), and HomeMesh sets it up
for you: in *Away from home* click **Set up…**, then **Set up Tailscale**.
After the administrator password it:
- installs Tailscale with the system's package manager (pacman, apt, dnf,
  zypper, xbps or apk) or, if the distribution has no package, Tailscale's
  own static build from pkgs.tailscale.com, checked against its sha256;
- starts `tailscaled` at boot with systemd, OpenRC, runit, s6 or dinit,
  writing the service definition itself when the package has none for that
  init system (Artix, for example);
- makes you Tailscale's operator, so the login and later changes need no
  password;
- opens the login page in your browser and carries on once you have logged in.

Do the same on every computer, with the same account (`homemesh away` does it
in a terminal). While the computers are at home they exchange their VPN
addresses. From then on every connection first checks
the local network (about 40 ms at home; away from home the check gives up
after about a second) and uses the VPN only when the other computer is not
there. Away from home, files travel over your home's internet
uplink, so large files are slower than at home.

## What other computers see

- **The whole home folder** (default): the same access as your own account,
  settings and keys included. Pair only your own computers.
- **Only chosen folders**, each read-write or read-only. Paired computers then
  get SFTP only, served by `sftp-server` inside a [bubblewrap](https://github.com/containers/bubblewrap)
  sandbox that holds an otherwise empty home folder with just those folders.
  There is no shell, no port forwarding, no `~/.ssh`, no `/etc` beyond a
  one-line `passwd`. A change ends running sessions, so it applies at once.

## Command line

    homemesh                 # the window (on a desktop)
    homemesh status          # this computer and the group
    homemesh enable          # turn sharing on (asks for the admin password)
    homemesh disable         # turn it off, restoring the previous settings
    homemesh pair            # pick a computer nearby and pair with a code
    homemesh pair --wait     # wait for another computer to pair with this one
    homemesh pair NAME|ADDRESS[:PORT]
    homemesh remove NAME     # remove a computer from the group
    homemesh sync            # catch up with the group now
    homemesh share           # what other computers see
    homemesh share add ~/Public [--readonly]
    homemesh share remove ~/Public
    homemesh share home      # back to the whole home folder
    homemesh autostart on|off
    homemesh rename NAME     # rename this computer on the network
    homemesh away            # set up Tailscale for access from away
    homemesh reset           # leave the group and remove HomeMesh's settings

## How it works

**Turning sharing on** runs a small root helper (`/usr/lib/homemesh/homemesh-helper`)
through polkit, or `sudo` in a terminal. It:
- makes `sshd` accept key logins only (`/etc/ssh/sshd_config.d/10-homemesh.conf`);
- starts it at boot, with systemd, OpenRC, runit, s6 or dinit;
- opens the firewall (ufw or firewalld) for the local network: 22/tcp,
  41753/udp and 41753–41762/tcp; and SSH on the VPN interfaces;
- turns on mDNS if `name.local` does not work yet, in systemd-resolved
  (`/etc/systemd/resolved.conf.d/zz-homemesh-mdns.conf`; being last, it also
  overrides a `MulticastDNS=no` set there before) or else with Avahi.

*Turn off* puts back what was there before. Setting up Tailscale goes through
the same helper (see [Away from home](#away-from-home)); turning sharing off
leaves Tailscale alone.

**Finding computers**: while the window is open, HomeMesh announces itself on
the local network over UDP broadcast. This works without Avahi. Broadcasting
stops once the window is closed.

**Pairing** swaps SSH keys over TCP. The code is computed from everything
swapped, and the answering side first commits to its random number and reveals
it only afterwards. This is the numeric comparison scheme of Bluetooth: someone
in the middle has one chance in a million of producing matching codes.

**Files**: each side writes
- the other's key into `~/.ssh/authorized_keys`, valid only from private
  addresses and from that computer's own VPN addresses;
- its host key into `~/.config/homemesh/known_hosts`, so there is never an
  "are you sure you trust this host" question;
- an entry into `~/.config/homemesh/ssh_config`, included at the top of
  `~/.ssh/config`;
- bookmarks for GTK (`~/.config/gtk-3.0/bookmarks`) and KDE
  (`~/.local/share/user-places.xbel`).

HomeMesh logs in with a key of its own (`~/.config/homemesh/id_ed25519`). It
never touches your keys or any line it did not write.

**The group**: every computer has a card (name, user, home folder, keys, VPN
addresses). Whenever members talk (window open, login, pairing), they swap
what they know through `ssh … homemesh sync-serve`. The newer card wins, and
removals leave a dated tombstone so they spread too.

**At login**: a short task (XDG autostart, can be turned off) catches up with
the group and mounts the computers marked *Connect at login*, then exits.

**Computers that are off**: the same setting starts `homemesh watch`, one
process of about 15 MB that ends with the desktop session. It puts *(off)* in
front of a bookmark's name, where no file manager cuts it off, and takes it
away again; names you gave the bookmarks stay. A computer that is on is asked
once a minute with a single UDP packet to its own address (41753/udp); nothing
is broadcast, and nothing is logged by its `sshd`. One that is off is looked up
every five minutes, but is noticed at once when it starts, because it asks the
others. Only paired computers get answers. Computers with HomeMesh older than
0.5, or with nobody logged in, are checked with an SSH connection every three
minutes instead.

## Security

- With the whole home folder shared, paired computers trust each other
  completely: write access to a home folder is as good as running programs
  there. Pair only your own computers, or share chosen folders.
- Keys work only from private addresses and from the peer's own VPN addresses.
  `sshd` stops accepting passwords over the network.
- Pairing and discovery only accept the local network (and Tailscale/NetBird
  addresses for pairing by address from a terminal).

## Status

Version 0.2: young. It has been tried between two Arch-based computers, one
with GNOME and one with KDE Plasma; Dolphin's side and the systems without
systemd (OpenRC, runit, s6, dinit) are written but not yet tried for real.
Reports are welcome.

## Uninstall

    homemesh reset && homemesh disable
    sudo pacman -R homemesh      # or homemesh-git

## Development

    make test                            # 123 tests; the integration tests run private sshd instances
    packaging/arch/build-local.sh        # package the working tree
    homemesh --demo --screenshot x.png   # render the window with made-up computers

## Translations

Messages are in English in the code and translated with gettext; Polish is
in `po/pl.po`. To add a language: `make pot`, copy `po/homemesh.pot` to
`po/<language>.po`, translate it (keep the `{fields}`), then `make mo` and
run with `LANGUAGE=<language> bin/homemesh`. `make test` checks that every
message is translated and keeps its fields.

## Writing about HomeMesh

Ready-made descriptions in English and Polish, from one line to a full
feature list, are in [docs/descriptions.md](docs/descriptions.md); screenshots are in
[docs/screenshots](docs/screenshots).

## License

MIT
