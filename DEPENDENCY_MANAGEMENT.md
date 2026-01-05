# Touchpoint Dependency Management

## Overview

The Touchpoint NVDA addon requires external Python dependencies (numpy, dxcam, opencv-python) that are not part of the standard NVDA installation. This document explains how dependencies are managed, based on the proven approach used by the AI-content-describer addon.

## How It Works

The addon uses a `dependency_checker.py` module that:

1. **Checks for dependencies** when NVDA starts
2. **Prompts the user** to install if missing (first run only)
3. **Installs to NVDA's config directory** (not the addon folder)
4. **Adds the path to sys.path** automatically for imports

## Installation Locations

### Option 1: Direct to NVDA Python (Recommended for Development)
- **Location**: NVDA's site-packages directory
- **Method**: Use NVDA Python Console or pip
- **Advantage**: Most reliable, works immediately
- **Use case**: Development and testing

### Option 2: Config Directory (For Distribution)
- **Location**: `%APPDATA%\nvda\touchpoint-deps-py3.11\`
- **Method**: Download pre-built ZIP package
- **Advantage**: Doesn't modify NVDA installation
- **Use case**: End-user distribution

## Developer Setup

### Method 1: NVDA Python Console (Easiest)

1. Open NVDA
2. Press `NVDA+Control+Z` to open Python Console
3. Run this script:

```python
import subprocess, sys
subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'numpy', 'dxcam', 'opencv-python'])
```

4. Restart NVDA

### Method 2: Using Test Script

1. Open NVDA Python Console (NVDA+Control+Z)
2. Run:
```python
exec(open(r'C:\Users\carso\Documents\GitHub\Project-Touchstone\Touchpoint-Software\testing\install_nvda_deps.py').read())
```

3. Restart NVDA

### Method 3: Manual pip Install

1. Locate NVDA's Python executable:
   - Typically: `C:\Program Files (x86)\NVDA\pythonw.exe`
   - Or portable: `[NVDA folder]\pythonw.exe`

2. Run from Windows PowerShell:
```powershell
& "C:\Program Files (x86)\NVDA\pythonw.exe" -m pip install numpy dxcam opencv-python
```

3. Restart NVDA

## Why This Approach?

### The Problem with `lib` Folder

Our previous attempts to bundle dependencies in a `lib` folder failed because:

- **Binary extensions (.pyd files)** cannot be reliably imported from custom paths
- Python's import system requires compiled extensions to be in `site-packages`
- Using `pip install --target lib/` works for pure Python, but not C extensions
- Numpy, OpenCV, and DXcam all use compiled C/C++ extensions

### The Solution

The AI-content-describer addon solved this by:

1. **Installing to NVDA's config directory** instead of addon folder
2. **Using pre-built packages** from GitHub releases
3. **Checking on first run** and prompting for installation
4. **Adding config path to sys.path** before importing

This works because:
- Config directory is writable (addon folder might not be)
- Packages are installed properly, not just copied
- Binary extensions can load their dependencies correctly
- User doesn't need admin rights

## Creating Distribution Packages

To distribute the addon with automatic dependency installation:

1. **Build dependency package**:
```powershell
pip download numpy dxcam opencv-python --platform win_amd64 --python-version 3.11 --only-binary=:all: -d deps
# Create ZIP of the downloaded wheels
```

2. **Create GitHub release** with dependency packages:
   - `touchpoint-deps-py3.11.zip` (for NVDA 2024+)
   - Upload to GitHub releases

3. **Update dependency_checker.py**:
```python
def get_dependencies_url():
    return "https://github.com/YOUR_USERNAME/YOUR_REPO/releases/download/deps/touchpoint-deps-py3.11.zip"
```

4. Users will be prompted to download on first run

## Troubleshooting

### Dependencies Won't Import
- Verify installation: Open NVDA Python Console and try `import numpy`
- Check NVDA log for error messages
- Try reinstalling with `--force-reinstall` flag

### Version Mismatch
- Ensure dependencies match NVDA's Python version (3.11 for NVDA 2024+)
- Check version: In Python Console, run `import sys; print(sys.version)`

### Permission Errors
- Run NVDA as administrator (only for installation)
- Or use config directory method (doesn't require admin)

## References

- [AI-content-describer dependency_checker.py](https://github.com/cartertemm/AI-content-describer/blob/main/addon/globalPlugins/AIContentDescriber/dependency_checker.py)
- NVDA Python is 3.11 (as of NVDA 2024.1)
- DXcam requires numpy and opencv-python
