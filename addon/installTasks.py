# -*- coding: utf-8 -*-
# Installation tasks for Touchpoint NVDA addon

import os
import sys
import wx
import addonHandler
import globalVars
import gui

addonHandler.initTranslation()

def onInstall():
    """Called when the addon is installed."""
    # Import dependency checker
    addon_path = os.path.dirname(__file__)
    sys.path.insert(0, os.path.join(addon_path, 'globalPlugins'))
    
    try:
        import dependency_checker
        # The dependency checker will prompt the user on next NVDA startup
        # We don't check here to avoid blocking the installation process
    except ImportError:
        pass
    
    # Clean up sys.path
    if os.path.join(addon_path, 'globalPlugins') in sys.path:
        sys.path.remove(os.path.join(addon_path, 'globalPlugins'))
