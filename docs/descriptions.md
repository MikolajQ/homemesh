# Descriptions · Opisy

Ready-to-use texts about HomeMesh for app catalogues, forums, package
listings and social media, in English and Polish, from one line to a full
feature list.

Gotowe teksty o HomeMesh do katalogów aplikacji, forów, list pakietów i
mediów społecznościowych, po angielsku i po polsku: od jednej linijki do
pełnej listy funkcji.

- Repository: https://github.com/MikolajQ/homemesh
- AUR: https://aur.archlinux.org/packages/homemesh (release), https://aur.archlinux.org/packages/homemesh-git (development)
- Interface: English and Polish
- Screenshots: `docs/screenshots/en/` and `docs/screenshots/pl/` (light and dark)
- License: MIT

---

## English

### Name and tagline

**HomeMesh**: the home folders of your computers in any file manager.

### One line (up to 100 characters)

Open your other computers' home folders in any Linux file manager; pair them with a code.

### Short (up to 300 characters)

HomeMesh puts the home folders of all your Linux computers in your file
manager's sidebar and marks the ones that are off. Pair two computers once
with a six-digit code, like Bluetooth, and browse and edit files across
them, at home or through a VPN. Plain SSH/SFTP underneath, GTK 4.

### Paragraph

HomeMesh makes the home folders of all your Linux computers appear in the
sidebar of Nautilus, Dolphin, Thunar, Nemo or Caja. Turn sharing on, open
HomeMesh on the other computer, click Connect and check that both screens
show the same six-digit code: that is the whole setup. A new computer only
has to be paired with one member of the group to know all of them, and a
computer that is off is marked “(off)”, so you see it before you click. At
home the files travel over the local network; away from home, through a VPN,
with the same bookmarks: one you already use, or Tailscale, which HomeMesh
sets up at a click. Underneath is plain SSH/SFTP, so there is no Samba or
NFS to configure and no server to look after. You can share the whole home
folder or only chosen folders, read-write or read-only, served from a
sandbox. Whatever is missing is installed with one button.

### Features

- **Setup in three clicks**: turn on, open on the second computer, connect
  and compare a code. A bar at the top always says what to do next.
- **Pairing like Bluetooth**: computers swap SSH keys and confirm a
  six-digit code; matching codes mean nobody tampered with the exchange.
  No passwords, no addresses to type.
- **Introductions**: pair a new computer with any member and the whole group
  learns about it; removals spread the same way.
- **In your file manager**: bookmarks for GTK file managers (GVfs) and
  Dolphin (KIO); a click mounts, optionally at login.
- **Shows what is off**: a computer that is off is called “(off) laptop” in
  the sidebar until it comes back. One packet a minute, nothing broadcast.
- **Choose what to share**: the whole home folder, or chosen folders, each
  read-write or read-only, served by sftp-server in a bubblewrap sandbox
  with no shell and no access to anything else.
- **Away from home**: sets up Tailscale with one click (install, service and
  login, no commands, with or without systemd) and also works with NetBird,
  ZeroTier, Yggdrasil, Nebula or WireGuard; each connection uses the local
  network when it can and the VPN otherwise.
- **Copes with a shaky network**: where the local network keeps losing the way
  between two computers, connections go through the VPN first and survive it;
  the window says what helps.
- **Nothing to type**: when a package is missing, one button installs it with
  the distribution's own package manager (pacman, apt, dnf, zypper, xbps or
  apk), and “Fix” repairs what stops other computers from finding this one.
- **Light and universal**: GTK 4 without libadwaita, fits any desktop; no
  system daemon (the system's sshd serves the files, and one small process in
  your session marks the computers that are off); works with systemd, OpenRC,
  runit, s6 or dinit; ufw and firewalld handled.
- **Terminal too**: `homemesh status`, `pair`, `share`, `remove`… for
  computers without a screen.
- **Careful with your system**: its own SSH key and config file; never
  touches your keys or lines it did not write; turning sharing off restores
  the previous settings.

### For technical audiences

HomeMesh is a small Python/GTK 4 app around OpenSSH. Pairing uses a
commit-then-reveal exchange like Bluetooth's numeric comparison, so a
man in the middle has a one-in-a-million chance of matching codes. Keys are
written with `from=` limited to private addresses and the peer's VPN
addresses; host keys are pinned with `HostKeyAlias`. Members gossip their
cards (keys, home folder, VPN addresses) over `ssh … homemesh sync-serve`,
with newest-wins merging and dated tombstones. Away from home, a
`Match originalhost … !exec` block falls back to the VPN address when a
quick local check fails (about 40 ms at home; away it gives up after about
a second). Where the local network broke twice in three days (the peer
answers only through the VPN, then locally again), the VPN is tried first.
Presence is a unicast UDP ping a minute between paired computers,
answered to them only; a session process keeps the “(off)” marks in the GTK
bookmarks and KDE's places and ends with the Wayland or X11 display. A root
helper behind polkit configures sshd (keys only), the firewall and mDNS, and
installs missing packages. 158 tests, including simulated computers with
private sshd instances.

### Keywords

linux, file sharing, home folder, LAN, SSH, SFTP, GVfs, KIO, Nautilus,
Dolphin, Thunar, GTK 4, Tailscale, VPN, Samba alternative, NFS alternative,
Arch Linux, AUR, polkit, bubblewrap

### Category suggestions

Network · File sharing · Utilities · System tools

---

## Polski

### Nazwa i hasło

**HomeMesh**: katalogi domowe twoich komputerów w każdym menedżerze plików.

### Jedna linijka (do 100 znaków)

Katalogi domowe twoich komputerów w menedżerze plików Linuksa; łączysz je kodem.

### Krótko (do 300 znaków)

HomeMesh umieszcza katalogi domowe wszystkich twoich komputerów z Linuksem
w panelu bocznym menedżera plików i oznacza wyłączone. Połącz dwa komputery
raz sześciocyfrowym kodem, jak Bluetooth, i przeglądaj oraz edytuj pliki
między nimi, w domu albo przez VPN. Pod spodem SSH/SFTP, GTK 4.

### Akapit

HomeMesh sprawia, że katalogi domowe wszystkich twoich komputerów z Linuksem
pojawiają się w panelu bocznym Nautilusa, Dolphina, Thunara, Nemo albo Caja.
Włączasz udostępnianie, otwierasz HomeMesh na drugim komputerze, klikasz
„Połącz” i sprawdzasz, czy oba ekrany pokazują ten sam sześciocyfrowy kod:
to cała konfiguracja. Nowy komputer wystarczy połączyć z jednym członkiem
grupy, a pozna wszystkich; wyłączony ma dopisek „(off)”, więc widać to przed
kliknięciem. W domu pliki płyną przez sieć lokalną, poza domem przez VPN, z
tymi samymi zakładkami: ten, którego już używasz, albo Tailscale, który
HomeMesh ustawia jednym kliknięciem. Pod spodem działa zwykłe SSH/SFTP, więc
nie trzeba konfigurować Samby ani NFS ani utrzymywać serwera. Możesz
udostępnić cały katalog domowy albo tylko wybrane foldery, do zapisu lub tylko
do odczytu, podawane z piaskownicy. Czego brakuje, instaluje jeden przycisk.

### Funkcje

- **Konfiguracja w trzech kliknięciach**: włącz, otwórz na drugim
  komputerze, połącz i porównaj kod. Pasek u góry zawsze podpowiada następny
  krok.
- **Parowanie jak Bluetooth**: komputery wymieniają klucze SSH i potwierdzają
  sześciocyfrowy kod; zgodne kody oznaczają, że nikt nie podmienił danych.
  Bez haseł i bez wpisywania adresów.
- **Przedstawianie**: nowy komputer połączony z dowolnym członkiem grupy staje
  się znany całej grupie; usunięcia rozchodzą się tak samo.
- **W twoim menedżerze plików**: zakładki dla menedżerów GTK (GVfs) i
  Dolphina (KIO); kliknięcie montuje, opcjonalnie po zalogowaniu.
- **Widać, co jest wyłączone**: wyłączony komputer nazywa się w panelu
  „(off) laptop”, dopóki nie wróci. Jeden pakiet na minutę, bez rozgłaszania.
- **Wybierasz, co udostępniasz**: cały katalog domowy albo wybrane foldery,
  każdy do zapisu lub tylko do odczytu, podawane przez sftp-server w
  piaskownicy bubblewrap, bez powłoki i bez dostępu do czegokolwiek innego.
- **Poza domem**: ustawia Tailscale jednym kliknięciem (instalacja, usługa i
  logowanie, bez poleceń, z systemd i bez niego), działa też z NetBird,
  ZeroTier, Yggdrasil, Nebula albo WireGuard; każde połączenie idzie przez
  sieć lokalną, gdy się da, a w przeciwnym razie przez VPN.
- **Radzi sobie z niestabilną siecią**: gdy sieć lokalna gubi drogę między
  dwoma komputerami, połączenia idą najpierw przez VPN i to przeżywają, a okno
  podpowiada, co pomaga.
- **Bez wpisywania poleceń**: brakujący pakiet instaluje jeden przycisk,
  menedżerem pakietów twojej dystrybucji (pacman, apt, dnf, zypper, xbps albo
  apk), a „Napraw” usuwa to, przez co inne komputery nie widzą tego.
- **Lekki i uniwersalny**: GTK 4 bez libadwaita, pasuje do każdego pulpitu;
  bez demona w systemie (pliki podaje systemowy sshd, a jeden mały proces w
  twojej sesji oznacza wyłączone komputery); działa z systemd, OpenRC, runit,
  s6 i dinit; obsługuje ufw i firewalld.
- **Także w terminalu**: `homemesh status`, `pair`, `share`, `remove`… dla
  komputerów bez ekranu.
- **Ostrożny z systemem**: własny klucz SSH i własny plik konfiguracji; nie
  rusza twoich kluczy ani linii, których sam nie dopisał; wyłączenie
  udostępniania przywraca poprzednie ustawienia.

### Dla technicznych odbiorców

HomeMesh to mała aplikacja w Pythonie i GTK 4 wokół OpenSSH. Parowanie
korzysta z wymiany „zobowiąż się, potem ujawnij”, jak porównanie liczb w
Bluetooth, więc atakujący pośrodku ma jedną szansę na milion na zgodne kody.
Klucze trafiają do authorized_keys z `from=` ograniczonym do adresów
prywatnych i adresów VPN danego komputera; klucze hostów są przypięte przez
`HostKeyAlias`. Członkowie wymieniają się kartami (klucze, katalog domowy,
adresy VPN) przez `ssh … homemesh sync-serve`; wygrywa nowsza karta, a
usunięcia zostawiają datowane ślady. Poza domem blok
`Match originalhost … !exec` przełącza na adres VPN, gdy szybkie sprawdzenie
sieci lokalnej się nie powiedzie (w domu około 40 ms; poza domem rezygnuje po
mniej więcej sekundzie). Gdy sieć lokalna zerwała się dwa razy w ciągu trzech
dni (komputer odpowiada tylko przez VPN, a potem znowu lokalnie), SSH próbuje
najpierw VPN-u. Obecność to jedno zapytanie UDP na minutę, wprost na
adres połączonego komputera; odpowiedź dostają tylko połączone. Proces w sesji
utrzymuje dopiski „(off)” w zakładkach GTK i miejscach KDE i kończy się razem z
Waylandem albo X11. Pomocnik z uprawnieniami roota za polkit ustawia sshd
(tylko klucze), zaporę i mDNS oraz instaluje brakujące pakiety. 158 testów, w
tym symulowane komputery z prywatnymi instancjami sshd.

### Słowa kluczowe

linux, udostępnianie plików, katalog domowy, sieć lokalna, LAN, SSH, SFTP,
GVfs, KIO, Nautilus, Dolphin, Thunar, GTK 4, Tailscale, VPN, alternatywa dla
Samby, alternatywa dla NFS, Arch Linux, AUR, polkit, bubblewrap

### Proponowane kategorie

Sieć · Udostępnianie plików · Narzędzia · Narzędzia systemowe
