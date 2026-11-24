"""When this computer's Wi-Fi joins an access point.

The kernel tells everyone (nl80211 events), no rights and no polling needed.
"""

import socket
import struct

NETLINK_GENERIC = 16
GENL_ID_CTRL, CTRL_CMD_GETFAMILY = 16, 3
CTRL_ATTR_FAMILY_NAME, CTRL_ATTR_MCAST_GROUPS = 2, 7
CTRL_ATTR_MCAST_GRP_NAME, CTRL_ATTR_MCAST_GRP_ID = 1, 2
SOL_NETLINK, NETLINK_ADD_MEMBERSHIP = 270, 1
NLMSG_ERROR = 2
JOINED = (38, 46, 47)  # NL80211_CMD_ASSOCIATE, CONNECT, ROAM


def _messages(data):
    """(type, command, attributes) of each generic netlink message."""
    at = 0
    while at + 20 <= len(data):
        length, kind = struct.unpack_from("IH", data, at)
        if length < 20 or at + length > len(data):
            return
        yield kind, data[at + 16], data[at + 20:at + length]
        at += (length + 3) & ~3


def _attributes(data):
    out, at = {}, 0
    while at + 4 <= len(data):
        length, kind = struct.unpack_from("HH", data, at)
        if length < 4 or at + length > len(data):
            break
        out[kind & 0x3fff] = data[at + 4:at + length]
        at += (length + 3) & ~3
    return out


def group_id(answer):
    """The number of nl80211's "mlme" group in the kernel's answer, or None."""
    for kind, _command, data in _messages(answer):
        if kind == NLMSG_ERROR:
            continue
        groups = _attributes(_attributes(data).get(CTRL_ATTR_MCAST_GROUPS, b""))
        for group in map(_attributes, groups.values()):
            number = group.get(CTRL_ATTR_MCAST_GRP_ID, b"")
            if group.get(CTRL_ATTR_MCAST_GRP_NAME, b"").rstrip(b"\0") == b"mlme" and len(number) >= 4:
                return struct.unpack_from("I", number)[0]
    return None


def joined(data):
    """Do these events say that the Wi-Fi has joined an access point?"""
    return any(command in JOINED for _kind, command, _data in _messages(data))


def events():
    """A non-blocking socket with the events, or None without Wi-Fi."""
    try:
        sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_GENERIC)
    except OSError:
        return None
    try:
        sock.bind((0, 0))
        sock.settimeout(1)
        name = b"nl80211\0"
        attribute = struct.pack("HH", 4 + len(name), CTRL_ATTR_FAMILY_NAME) + name
        payload = struct.pack("BBH", CTRL_CMD_GETFAMILY, 1, 0) + attribute + b"\0" * (-len(attribute) % 4)
        sock.send(struct.pack("IHHII", 16 + len(payload), GENL_ID_CTRL, 1, 1, 0) + payload)  # NLM_F_REQUEST
        number = group_id(sock.recv(65536))
        if number is None:
            raise OSError("no nl80211")
        sock.setsockopt(SOL_NETLINK, NETLINK_ADD_MEMBERSHIP, number)
        sock.setblocking(False)
        return sock
    except OSError:
        sock.close()
        return None
