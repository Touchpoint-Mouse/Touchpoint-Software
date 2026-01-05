# Quick Start Guide - Touchpoint NVDA Addon Development

## Goal
Get the Touchpoint addon running in NVDA with proper dependency management.

## Prerequisites
- NVDA 2024+ installed (includes Python 3.11)
- Songbird UART device connected to COM6
- This repository cloned

## Step 1: Install Dependencies

### Option A: Using NVDA Python Console (Recommended)

1. Launch NVDA
2. Press `NVDA+Control+Z` to open Python Console
3. Copy and paste this command:
```python
import subprocess, sys; subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'numpy', 'dxcam', 'opencv-python'])
```
4. Wait for installation to complete
5. Close the console and restart NVDA

### Option B: Using Test Script

1. Open NVDA Python Console (`NVDA+Control+Z`)
2. Run:
```python
exec(open(r'C:\Users\carso\Documents\GitHub\Project-Touchstone\Touchpoint-Software\testing\install_nvda_deps.py').read())
```
3. Restart NVDA

## Step 2: Verify Dependencies

1. Open NVDA Python Console again
2. Test imports:
```python
import numpy as np
import dxcam
print(f"NumPy {np.__version__} - OK")
print(f"DXcam - OK")
```

If you see version numbers, you're good!

## Step 3: Install the Addon

### Method 1: Development Copy (Fastest)

1. Find NVDA's addon directory:
   - Installed: `%APPDATA%\nvda\addons\`
   - Portable: `[NVDA folder]\userConfig\addons\`

2. Create a folder named `touchpoint`

3. Copy contents of `addon\` folder to `touchpoint\`:
```powershell
# From PowerShell
xcopy "C:\Users\carso\Documents\GitHub\Project-Touchstone\Touchpoint-Software\addon\*" "%APPDATA%\nvda\addons\touchpoint\" /E /I /Y
```

4. Restart NVDA

### Method 2: Create .nvda-addon Package

1. Create ZIP of addon folder contents (manifest.ini should be at root)
2. Rename from `.zip` to `.nvda-addon`
3. Double-click to install
4. Restart NVDA

## Step 4: Test the Addon

1. Check NVDA log (NVDA → Tools → View Log):
   - Should see: "Touchpoint NVDA addon initialized"
   - Should see: "Ping received from microcontroller"
   - Should see: "DXcam initialized"

2. Test functionality:
   - Open a web browser with images
   - Move mouse over an image
   - Should feel vibration pulse on enter
   - Elevation feedback should vary with mouse position

## Troubleshooting

### "Dependencies not available"

**Symptom**: Error message about missing dependencies

**Solution**:
1. Verify installation: NVDA Console → `import numpy, dxcam`
2. Reinstall: `subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--force-reinstall', 'numpy', 'dxcam', 'opencv-python'])`

### "Failed to open serial port"

**Symptom**: Addon loads but no UART connection

**Solution**:
1. Check COM port in Device Manager
2. Update `SERIAL_PORT` in touchpoint.py if not COM6
3. Verify Songbird device is connected
4. Try running `testing/serial_plotter.py` separately to test connection

### Addon doesn't load

**Symptom**: No log messages, no errors

**Solution**:
1. Check addon is in correct folder
2. Verify `manifest.ini` exists
3. Check NVDA log for syntax errors
4. Try: NVDA → Tools → Manage add-ons → Refresh

## File Structure Check

Your addon folder should look like:
```
touchpoint/
├── manifest.ini
├── buildVars.py
├── README.md
├── installTasks.py
└── globalPlugins/
    ├── __init__.py
    ├── touchpoint.py
    └── dependency_checker.py
```

## Next Steps

Once working:
1. Test screen capture with various images
2. Verify elevation feedback accuracy
3. Test border vibration on screen edges
4. Optimize depth map processing if needed

## Reference

- Full docs: [DEPENDENCY_MANAGEMENT.md](DEPENDENCY_MANAGEMENT.md)
- Implementation: [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)
- Source: [AI-content-describer](https://github.com/cartertemm/AI-content-describer)
