#!/usr/bin/env python3
"""Menu bar status icon for WhisperPTT. Uses PyObjC directly (no rumps)."""
import os
import signal
import sys

from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from Foundation import NSObject, NSRunLoop, NSTimer, NSDefaultRunLoopMode
from PyObjCTools import AppHelper

NSApplication.sharedApplication().setActivationPolicy_(
    NSApplicationActivationPolicyAccessory
)

_script_dir = os.path.dirname(os.path.abspath(__file__))
_status_file = os.path.join(_script_dir, ".whisper_ptt_status")

TITLES = {"idle": "🎤", "recording": "🔴", "transcribing": "⏳"}


class AppDelegate(NSObject):
    statusItem = None
    _last_state = "idle"

    def applicationDidFinishLaunching_(self, notification):
        self.statusItem = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self.statusItem.button().setTitle_(TITLES["idle"])

        menu = NSMenu.alloc().init()
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit WhisperPTT", "quitApp:", "q"
        )
        quit_item.setTarget_(self)
        menu.addItem_(quit_item)
        self.statusItem.setMenu_(menu)

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.5, self, "pollStatus:", None, True
        )

    def pollStatus_(self, timer):
        try:
            with open(_status_file) as f:
                state = f.read().strip()
        except FileNotFoundError:
            state = "idle"

        if state == "quit":
            NSApplication.sharedApplication().terminate_(None)
            return

        if state != self._last_state:
            self._last_state = state
            title = TITLES.get(state, TITLES["idle"])
            self.statusItem.button().setTitle_(title)

    def quitApp_(self, sender):
        try:
            ppid = os.getppid()
            if ppid > 1:
                os.kill(ppid, signal.SIGTERM)
        except Exception:
            pass
        NSApplication.sharedApplication().terminate_(None)


def main():
    signal.signal(signal.SIGTERM, lambda *_: AppHelper.stopEventLoop())
    app = NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
