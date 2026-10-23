"""Says whether the focused spot in a Chromium window will take typed text.

Typed text reaches a web page as key presses. With no text box focused, the
page treats them as keys: every space is Page Down, so a sentence scrolls the
page toward the bottom. Chrome, Edge and Electron apps tell UI Automation what
has focus, and mark the page itself (and buttons, links, read-only fields) as
read-only, so VoiceWriter can tell before it types.

Other apps are left alone: their UI Automation support varies too much to trust
a "no", and none of them scroll on a space.
"""

import logging
import time

log = logging.getLogger("voicewriter.focus")

STATE_SYSTEM_READONLY = 0x40
UIA_PaneControlTypeId = 50033

_uia = None
_U = None


def _client():
    """The UI Automation client for this thread, made on first use (COM objects stay on their thread)."""
    global _uia, _U
    if _uia is None:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U
        _U = U
        _uia = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
    return _uia


def _read(el) -> bool | None:
    U = _U
    if el.CurrentControlType == UIA_PaneControlTypeId:
        # The window frame itself: Chrome is still building its accessibility tree.
        return None
    state = el.GetCurrentPropertyValue(U.UIA_LegacyIAccessibleStatePropertyId) or 0
    has_text = el.GetCurrentPropertyValue(U.UIA_IsTextPatternAvailablePropertyId)
    return bool(has_text) and not state & STATE_SYSTEM_READONLY


def takes_text() -> bool:
    """False only when a Chromium window has focus on something that isn't a text field."""
    try:
        uia = _client()
        for attempt in range(4):
            el = uia.GetFocusedElement()
            if el.CurrentFrameworkId != "Chrome":
                return True
            answer = _read(el)
            log.debug("focus: %s %s -> %s", el.CurrentLocalizedControlType, el.CurrentClassName, answer)
            if answer is not None:
                if not answer:
                    log.info("no text field focused (%s), not typing", el.CurrentLocalizedControlType)
                return answer
            time.sleep(0.1)
        return True
    except Exception:
        # COM errors come in many shapes. If UI Automation can't answer, type as before rather than lose the sentence.
        log.exception("focus check failed")
        return True
