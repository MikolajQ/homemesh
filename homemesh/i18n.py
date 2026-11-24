"""Translations (gettext). The messages in the code are English.

Installed, the catalogues are in /usr/share/locale; in a checkout, `make mo`
puts them in build/locale, which is used when it exists.
"""

import gettext
from pathlib import Path

DOMAIN = "homemesh"
_checkout = Path(__file__).resolve().parent.parent / "build" / "locale"
LOCALEDIR = str(_checkout) if _checkout.is_dir() else None

_ = gettext.translation(DOMAIN, localedir=LOCALEDIR, fallback=True).gettext
