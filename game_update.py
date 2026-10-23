"""Reading the Play Store and the installed game version, for updating the game when it asks for a new version.

The Play Store has no calibrated screens, so its buttons are found in a uiautomator dump of the window instead.
"""
import re
from xml.etree import ElementTree

PACKAGE = 'com.supercell.clashofclans'
STORE_PACKAGE = 'com.android.vending'
GAME_TITLE = 'Clash of Clans'
# The Play Store's update button, in English and Indonesian. Install buttons are never tapped: the store page also
# lists sponsored apps, and the game is always installed already.
UPDATE_LABELS = frozenset({'update', 'perbarui'})
BOUNDS = re.compile(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]')
VERSION_FIELDS = ('versionCode', 'versionName', 'lastUpdateTime')


def update_button(dump: bytes) -> list | None:
    """Centre of the Play Store's Update button for the game in a uiautomator dump: the in-app update dialog, or
    the game's store page, where the label sits inside the clickable button. None while it is not shown (the store
    still opening or already downloading) or on any other screen, such as a sign-in."""
    start, end = dump.find(b'<hierarchy'), dump.rfind(b'</hierarchy>')
    if start < 0 or end < start:
        return None
    try:
        root = ElementTree.fromstring(dump[start:end + len(b'</hierarchy>')])
    except ElementTree.ParseError:
        return None
    parents = {child: parent for parent in root.iter() for child in parent}
    nodes = list(root.iter('node'))
    if not any(GAME_TITLE in node.get('text', '') + node.get('content-desc', '') for node in nodes):
        return None
    for node in nodes:
        labels = {node.get('text', '').strip().lower(), node.get('content-desc', '').strip().lower()}
        if node.get('package') != STORE_PACKAGE or not labels & UPDATE_LABELS:
            continue
        button = node
        while button is not None and button.get('clickable') != 'true':
            button = parents.get(button)
        if button is None or button.get('enabled') != 'true' or button.get('package') != STORE_PACKAGE:
            continue
        match = BOUNDS.fullmatch(button.get('bounds', ''))
        if match:
            left, top, right, bottom = map(int, match.groups())
            return [(left + right) // 2, (top + bottom) // 2]
    return None


def installed_version(dumpsys: str) -> str | None:
    """'versionName (versionCode, lastUpdateTime)' of the installed game from `dumpsys package`; None while the
    package is missing, as it can be for a moment during the install."""
    values = {}
    for field in VERSION_FIELDS:
        match = re.search(rf'\b{field}=(\S+(?: \d\d:\d\d:\d\d)?)', dumpsys)
        if not match:
            return None
        values[field] = match.group(1)
    return f'{values["versionName"]} ({values["versionCode"]}, {values["lastUpdateTime"]})'
