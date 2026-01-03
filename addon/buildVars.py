# -*- coding: utf-8 -*-
# Build variables for Touchpoint NVDA addon

import os.path

# Add-on information variables
addon_info = {
    # Add-on name (internal name, no spaces)
    "addon_name": "Touchpoint",
    # Add-on summary, usually the user-visible name of the addon
    "addon_summary": "Touchpoint UI Event Monitor",
    # Add-on description
    "addon_description": "Monitors and logs NVDA UI element events for the Touchpoint project",
    # Version
    "addon_version": "1.0.0",
    # Author(s)
    "addon_author": "Carson Ray <carsonray314@gmail.com>",
    # URL for the add-on documentation
    "addon_url": "https://github.com/Project-Touchstone/Touchpoint-Software",
    # File name for the add-on documentation
    "addon_docFileName": "README.md",
    # Minimum NVDA version supported
    "addon_minimumNVDAVersion": "2019.3.0",
    # Last NVDA version supported/tested
    "addon_lastTestedNVDAVersion": "2024.1.0",
    # Add-on update channel (default is None, meaning no updates)
    "addon_updateChannel": None,
}

# Define the python files that are the sources of your add-on.
# You can use glob expressions here, they will be expanded.
pythonSources = [
    os.path.join("globalPlugins", "touchpoint.py"),
]

# Files that contain strings for translation. Usually your python sources
i18nSources = pythonSources + ["buildVars.py"]

# Files that will be ignored when building the add-on.
# Paths are relative to the add-on directory, not to the directory containing this file.
excludedFiles = []
