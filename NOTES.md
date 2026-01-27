### Basics of NVDA Addons

- [NVDA Developer Guide](https://download.nvaccess.org/documentation/developerGuide.html) is semi-helpful for the overall API capabilities but has very minimal examples
- Best way to start developing is to examine source code for existing addons or ask AI coding tools to create templates for functionality
- Important note: addons are essentially a collection of plugins, and each plugin is a python package. If there is more than one file inside a python package you need to specific the entry point within the __init__ py or else it will be treated as separate packages
- Steps for setting the right NVDA settings
    1. NVDA key is set to insert/numpad 0 by default
    2. Use NVDA+n and go Preferences>Settings
    3. In Vision tab enable all highlighting options to highlight focus (blue), navigator (red), and cursor (yellow)
    4. In Mouse enable report object when mouse enters it (this facilitates spatial scanning rather than keyboard based scanning)
    5. In Advanced check enable loading custom code from scratchpad directory
- Differentiating between main NVDA objects
    - Focus (blue): what object is currently accepting inputs/is clicked on
    - Cursor (yellow): where text is inserted
    - Navigator (red): purely accessibility concept, what the screen reader is currently reading (looking without touching)
- Steps for testing addon code:
    1. Copy contents of addon folder into NVDA scratchpad directory C:\Users\<user>\AppData\Roaming\nvda\scratchpad (replacing existing files)
    2. Run NVDA
    3. Use key command NVDA+F1 to open log for debugging
    4. Note: log window is not live and only updates when window is refocused. When window is refocused it also prints out all of the attributes of the current navigator object (red square)

### Dependency Issues

- Unfortunately since NVDA has its own python environment you cannot pre-install addon python dependencies other than what is already installed (specifically opencv and a screen capturing library are necessary)
- Worked around this by copying a method used by another NVDA addon [AI Content Describer](https://github.com/cartertemm/AI-content-describer/tree/main)
- Steps for adding dependencies
    1. Create an identical Python environment to NVDA (currently Python 3.11 32-bit) which is easiest using new [Windows Python Installer](https://www.python.org/downloads/) which allows CLI interface for installing specific python versions using py -m install 3.11-32 for example
    2. Install the desired dependencies in this python environment (make sure to use py -3.11 -m pip install <library> if you have multiple versions installed)
    3. Copy the entire library folder from C:\Users\<user>\AppData\Local\Python\<version>\Lib\site-packages
    4. Paste the library folder in a custom directly within the nvda root folder (C:\Users\<user>\AppData\Roaming\nvda
    5. Remove any _pycache_ folders from the library (not sure if this is strictly necessary but it may prevent conflicts when used on different machines)
    6. Also paste in deps folder in the software repo for sharing
    7. To use the dependency in the addon a program needs to temporatrily add the custom dependency directory to path (which is done automatically in the dependencies module, so you just need to add the dependency to the list of imports in dependencies and dependency checker modules)
    8. Note: the end user will not have this custom dependency folder so the addon needs to download it from an online github release (this has not been tested yet but copying the way it was done in the other addon should work)