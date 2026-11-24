# Gdy sieć lokalna się zrywa

W niektórych domowych sieciach dwa komputery tracą drogę do siebie na pół
minuty. Oba są włączone i mają internet, a jednak się nie widzą. Otwarty folder
zamiera, a potem zostaje odcięty; menedżer plików, który próbuje go otworzyć,
pisze coś w rodzaju *Nie można uzyskać dostępu do położenia*.

## Co robi z tym HomeMesh

Niczego nie trzeba ustawiać, ale oba komputery muszą mieć VPN (Tailscale
ustawisz w oknie, w sekcji *Poza domem*).

- **Od razu:** połączenie, które nie znajduje drogi w sieci lokalnej, idzie
  przez VPN.
- **Po drugim zerwaniu** w ciągu trzech dni SSH próbuje najpierw VPN-u, więc
  nic nie musi się najpierw nie udać. Pakiety VPN-u zostają w domu, a gdy sieć
  lokalna się zrywa, w kilka sekund znajdują inną drogę przez router: otwarte
  foldery zostają otwarte. Widać to w oknie i w `homemesh status`.
- **Po trzech dniach bez zerwania** wraca sieć lokalna.

Zerwanie to sytuacja, w której komputer odpowiadał w sieci lokalnej, potem
odpowiada tylko przez VPN, a najdalej po kwadransie znowu lokalnie. Komputery
mówią o tym sobie nawzajem, bo często zauważa to tylko jeden z nich: droga może
zniknąć w jednym kierunku. Nic nie jest do tego mierzone ani wysyłane poza
pakietami, które sprawdzają, kto jest włączony.

Zmierzone w jednej takiej sieci (Wi-Fi 6, dwa laptopy, trzypasmowy router
mesh), przy otwartym i bezczynnym połączeniu, gdy Wi-Fi jednego laptopa
przeszło na inne pasmo (dwa razy):

| | przez sieć lokalną | przez Tailscale |
| --- | --- | --- |
| otwarte połączenie | raz odcięte po 46 s, raz cisza 55 s | nic nie zginęło, cisza 7 i 4 s |
| prędkość jednego połączenia SSH | 175–234 Mb/s | 156–250 Mb/s |
| procesor, z jednego rdzenia | 8–14 % | 22–34 % |

## Skąd się to bierze

Zwykle z routera: komputer przechodzi z jednego pasma Wi-Fi na drugie, a router
dalej wysyła na to pierwsze wszystko, co mają dla niego inne komputery z tego
pasma. Jest gorzej, gdy komputer ciągle skacze między pasmami, choć stoi w
jednym miejscu.

Czy komputer tak robi (patrz na godziny, w których nie był zajęty: przy stałym
ruchu przeskoki potrafią ustać):

    journalctl -b -t wpa_supplicant | grep -c 'Associated with'

Więcej niż kilka na godzinę bez noszenia komputera to za dużo.

## Co pomaga

1. **Oprogramowanie (firmware) routera**, także w węzłach mesh: zaktualizuj je,
   a potem uruchom je ponownie.
2. **Osobna nazwa dla każdego pasma Wi-Fi** (2,4 GHz, 5 GHz, drugie 5 GHz; w
   routerach ASUS przy wyłączonym *Smart Connect*) i komputery na jednym z nich.
   Przestaną wtedy skakać między pasmami jednego
   routera, a nadal będą przechodzić między węzłami mesh, bo pasmo nazywa się
   na wszystkich tak samo.
3. **Kabel** do komputera, który stoi w jednym miejscu.
4. **Na komputerze z Linuksem, NetworkManagerem i wpa_supplicantem:** gdy sieć
   ma więcej niż jeden nadajnik, a są nimi już pasma jednego routera,
   NetworkManager każe szukać lepszego co 30 sekund, gdy tylko sygnał spadnie
   poniżej −65 dBm. To zwykły sygnał w sąsiednim pokoju. Wartość jest zaszyta w
   NetworkManagerze, ale można ją zmienić w działającym wpa_supplicancie, bez
   rozłączania:

       wpa_cli -i wlan0 set_network 0 bgscan '"simple:30:-75:86400"'

   Poniżej −75 dBm, gdzie nadajnik naprawdę przestaje sięgać, komputer nadal
   rozgląda się od razu. Żeby zmiana została, zapisz w
   `/etc/NetworkManager/dispatcher.d/60-wifi-roaming` (właściciel root, prawa
   755):

       #!/bin/bash
       [ "$2" = up ] && [ -e "/sys/class/net/$1/phy80211" ] || exit 0
       cli() { wpa_cli -p /run/wpa_supplicant -i "$1" "${@:2}" 2>/dev/null; }
       id=$(cli "$1" status | sed -n 's/^id=//p')
       [ -n "$id" ] && [ "$(cli "$1" get_network "$id" bgscan)" = '"simple:30:-65:300"' ] &&
           cli "$1" set_network "$id" bgscan '"simple:30:-75:86400"'

   Wyższy próg (−70) pasuje tam, gdzie jeden węzeł jeszcze słychać, a drugi
   jest dużo bliżej. HomeMesh sam tego nie robi: zmieniłby działanie Wi-Fi dla
   wszystkich programów i sieci.
