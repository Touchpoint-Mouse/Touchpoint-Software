# Touchpoint NVDA Addon

This NVDA addon captures UI element events for the Touchpoint project.

## Features

- Monitors focus changes on UI elements
- Captures value and state changes
- Tracks menu events
- Logs alerts and notifications
- Records keyboard input events
- Monitors document loading events

## Installation

1. Copy the entire `addon` folder contents into an NVDA addon package structure
2. Create a `.nvda-addon` package (which is essentially a ZIP file with the addon folder contents)
3. Install the `.nvda-addon` file through NVDA's addon manager (NVDA menu → Tools → Manage add-ons)

## Creating an NVDA Addon Package

To create a proper NVDA addon package:

1. Install the NVDA addon development tools:
   ```
   pip install markdown
   ```

2. Create a ZIP file with the addon contents:
   - The root should contain `manifest.ini`
   - Include the `globalPlugins` folder with the plugin code

3. Rename the `.zip` file to `.nvda-addon`

4. Install through NVDA

## Development

### File Structure

```
addon/
├── manifest.ini                    # Addon metadata
├── globalPlugins/
│   ├── __init__.py
│   └── touchpoint.py              # Main plugin code
└── README.md                       # This file
```

### Event Handlers

The addon implements various event handlers:

- `event_gainFocus` - Object receives focus
- `event_loseFocus` - Object loses focus
- `event_foreground` - Window comes to foreground
- `event_nameChange` - Object name changes
- `event_valueChange` - Object value changes
- `event_stateChange` - Object state changes
- `event_selection` - Selection is made
- `event_mouseMove` - Mouse movement (commented out by default due to frequency)
- `event_typedCharacter` - Character typed
- `event_caret` - Caret movement (commented out by default due to frequency)
- `event_menuStart` - Menu opened
- `event_menuEnd` - Menu closed
- `event_alert` - Alert appears
- `event_documentLoadComplete` - Document finishes loading

### Extending the Addon

To extend the functionality:

1. **Add custom event handlers**: Create additional `event_*` methods in the `GlobalPlugin` class
2. **External communication**: Modify the `logUIElement` method to send data to external systems (serial, TCP, file, etc.)
3. **Filtering events**: Add conditions to event handlers to filter specific applications or control types
4. **Add app modules**: Create application-specific modules in an `appModules` folder

## Integration with Touchpoint

This addon is designed to work with the Touchpoint project. To integrate:

1. Modify the `logUIElement` method to communicate with your depth map feedback system
2. Use serial communication, TCP sockets, or shared memory to send event data
3. Consider filtering events to only track relevant UI interactions

## Notes

- Some events (mouseMove, caret) are commented out by default as they fire very frequently
- Enable them if needed for your specific use case
- All events are logged to NVDA's log file (can be viewed in NVDA Log Viewer)
- The addon loads as a global plugin, meaning it runs for all applications

## Compatibility

- Minimum NVDA version: 2019.3.0
- Last tested NVDA version: 2024.1.0
- Platform: Windows

## License

Add your license information here.
