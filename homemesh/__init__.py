"""HomeMesh: the home folders of your computers in any file manager, over SSH.

Computers pair with a six-digit code, like Bluetooth devices. Paired
computers introduce each other, so a new computer paired with any member
of the group gets to know all of them.
"""

__version__ = "0.8.3"

APP_ID = "io.github.MikolajQ.HomeMesh"
APP_NAME = "HomeMesh"

# UDP for finding each other, TCP for pairing. A second user on the same
# computer gets the next free TCP port, so the firewall opens a small range.
PORT = 41753
PORT_SPAN = 10
