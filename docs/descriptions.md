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
manager's sidebar. Pair two computers once with a six-digit code, like
Bluetooth, and browse and edit files across them, at home or through your
VPN. Plain SSH/SFTP underneath, GTK 4, no daemon.

### Paragraph

HomeMesh makes the home folders of all your Linux computers appear in the
sidebar of Nautilus, Dolphin, Thunar, Nemo or Caja. Turn sharing on, open
HomeMesh on the other computer, click Connect and check that both screens
show the same six-digit code: that is the whole setup. A new computer only
has to be paired with one member of the group to know all of them. At home
the files travel over the local network; away from home, through the VPN
you already use, such as Tailscale, with the same bookmarks. Underneath is
plain SSH/SFTP, so there is no Samba or NFS to configure and no extra
service to run. You can share the whole home folder or only chosen folders,
read-write or read-only, served from a sandbox.

### Features

- **Setup in three clicks**: turn on, open on the second computer, connect
  and compare a code. A bar at the top always says what to do next.
- **Pairing like Bluetooth**: computers swap SSH keys and confirm a
  six-digit code; matching codes mean nobody tampered with the exchange.
  No passwords, no addresses to type.
- **Introductions**: pair a new computer with any member and the whole group
  learns about it; removals spread the same way.
- **In your file manager**: bookmarks for GTK file managers (GVfs) and
  Dolphin (KIO); a click mounts, optionally at login; computers that are
  off are marked “(off)”.
- **Choose what to share**: the whole home folder, or chosen folders, each
  read-write or read-only, served by sftp-server in a bubblewrap sandbox
  with no shell and no access to anything else.
- **Away from home**: sets up Tailscale with one click (install, service and
  login, no commands, with or without systemd) and also works with NetBird,
  ZeroTier, Yggdrasil, Nebula or WireGuard; each connection uses the local
  network when it can and the VPN otherwise.
- **Light and universal**: GTK 4 without libadwaita, fits any desktop; no
  system daemon (the system's sshd serves the files); works with systemd, OpenRC,
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
a second). A root helper behind polkit configures
sshd (keys only), the firewall and mDNS. 44 tests, including three
simulated computers with private sshd instances.

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
w panelu bocznym menedżera plików. Połącz dwa komputery raz sześciocyfrowym
kodem, jak Bluetooth, i przeglądaj oraz edytuj pliki między nimi, w domu albo
przez VPN. Pod spodem SSH/SFTP, GTK 4, bez demona.

### Akapit

HomeMesh sprawia, że katalogi domowe wszystkich twoich komputerów z Linuksem
pojawiają się w panelu bocznym Nautilusa, Dolphina, Thunara, Nemo albo Caja.
Włączasz udostępnianie, otwierasz HomeMesh na drugim komputerze, klikasz
„Połącz” i sprawdzasz, czy oba ekrany pokazują ten sam sześciocyfrowy kod:
to cała konfiguracja. Nowy komputer wystarczy połączyć z jednym członkiem
grupy, a pozna wszystkich. W domu pliki płyną przez sieć lokalną, poza domem
przez VPN, którego już używasz, na przykład Tailscale, z tymi samymi
zakładkami. Pod spodem działa zwykłe SSH/SFTP, więc nie trzeba konfigurować
Samby ani NFS ani utrzymywać dodatkowej usługi. Możesz udostępnić cały katalog
domowy albo tylko wybrane foldery, do zapisu lub tylko do odczytu, podawane z
piaskownicy.

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
  Dolphina (KIO); kliknięcie montuje, opcjonalnie po zalogowaniu; wyłączone
  komputery mają dopisek „(off)”.
- **Wybierasz, co udostępniasz**: cały katalog domowy albo wybrane foldery,
  każdy do zapisu lub tylko do odczytu, podawane przez sftp-server w
  piaskownicy bubblewrap, bez powłoki i bez dostępu do czegokolwiek innego.
- **Poza domem**: ustawia Tailscale jednym kliknięciem (instalacja, usługa i
  logowanie, bez poleceń, z systemd i bez niego), działa też z NetBird,
  ZeroTier, Yggdrasil, Nebula albo WireGuard; każde połączenie idzie przez
  sieć lokalną, gdy się da, a w przeciwnym razie przez VPN.
- **Lekki i uniwersalny**: GTK 4 bez libadwaita, pasuje do każdego pulpitu;
  bez demona w systemie (pliki podaje systemowy sshd); działa z systemd, OpenRC, runit,
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
mniej więcej sekundzie). Pomocnik z uprawnieniami roota
za polkit ustawia sshd (tylko klucze), zaporę i mDNS. 44 testy, w tym trzy
symulowane komputery z prywatnymi instancjami sshd.

### Słowa kluczowe

linux, udostępnianie plików, katalog domowy, sieć lokalna, LAN, SSH, SFTP,
GVfs, KIO, Nautilus, Dolphin, Thunar, GTK 4, Tailscale, VPN, alternatywa dla
Samby, alternatywa dla NFS, Arch Linux, AUR, polkit, bubblewrap

### Proponowane kategorie

Sieć · Udostępnianie plików · Narzędzia · Narzędzia systemowe
