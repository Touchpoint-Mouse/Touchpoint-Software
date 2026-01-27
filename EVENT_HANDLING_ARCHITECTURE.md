# Event Handling Architecture

## Overview

The Touchpoint NVDA addon implements a sophisticated event handling architecture that processes UI events and mouse interactions to provide haptic feedback through the Touchpoint hardware device. The architecture is built on a multi-layered design that separates concerns between event detection, filtering, handling, and effect execution.

## Core Components

### 1. **GlobalPlugin** (Main Entry Point)
Located in [touchpoint.py](addon/globalPlugins/touchpoint/touchpoint.py)

The `GlobalPlugin` class is the central orchestrator that:
- Initializes the plugin when NVDA starts
- Manages handler collections (object and global handlers)
- Runs background threads for event tracking and screen capture
- Interfaces with hardware driver for haptic output
- Tracks mouse position and current UI object state

**Key Responsibilities:**
- Event routing to appropriate handlers
- Thread management (capture thread, event thread)
- Mouse position tracking with thread-safe locks
- Screen region capture coordination
- Hardware driver lifecycle management

### 2. **Handler System**
Located in [handlers.py](addon/globalPlugins/touchpoint/handlers.py)

The handler system consists of three main handler types:

#### **HandlerManager**
- Manages collections of handlers
- Provides methods to add and populate handlers
- Links handlers to the parent plugin instance

#### **ObjectHandler**
Handles NVDA object-related events (UI elements):
- Uses a `filter` to determine which objects it applies to
- Contains a dictionary of `effects` mapped to event names
- Processes events like: `enter`, `leave`, `gainFocus`, `loseFocus`, `foreground`, `nameChange`, `valueChange`, `stateChange`, `selection`

#### **GlobalHandler**
Handles global events not tied to specific UI objects:
- Runs continuously via the `__call__()` method
- Checks conditions and triggers events programmatically
- Example: Screen border detection

#### **GraphicHandler** (Specialized ObjectHandler)
Extends `ObjectHandler` specifically for image/graphic elements:
- Captures screen regions when mouse enters graphics
- Processes images into depth maps using OpenCV
- Sends elevation data to hardware based on depth maps
- Integrates with screen capture system

### 3. **Filter System**
Located in [filters.py](addon/globalPlugins/touchpoint/filters.py)

Filters determine when handlers should activate:

#### **ObjectFilter**
- Base class for filtering NVDA objects
- `matches(plugin, obj)` returns `True` if handler should process the object
- Default implementation matches all objects

#### **GlobalFilter**
- Base class for filtering global conditions
- `matches(plugin)` returns `True` if handler should be active
- Default implementation matches all plugins

#### **ComboObjectFilter / ComboGlobalFilter**
- Combines multiple filters with include/exclude lists
- Provides boolean logic for complex filtering scenarios

#### **GraphicFilter** (Specialized ObjectFilter)
- Matches objects with roles: `GRAPHIC`, `IMAGEMAP`
- Checks IAccessible2 attributes for video tags
- Verifies objects have valid location data

### 4. **Effect System**
Located in [effects.py](addon/globalPlugins/touchpoint/effects.py)

Effects are the actions executed when events occur:

#### **Effect** (Base Class)
- Defines `__call__(handler, obj=None, **kwargs)` interface
- Receives handler context, optional NVDA object, and event parameters

#### **ComboEffect**
- Combines multiple effects to execute sequentially
- Allows complex multi-action responses to single events

#### **VibrationEffect**
- Sends vibration commands to Touchpoint hardware
- Parameters: amplitude (0.0-1.0), frequency (Hz), duration (ms)

#### **GlobalElevationEffect**
- Sets absolute elevation on Touchpoint device
- Overrides relative elevation values

#### **RelativeElevationEffect**
- Adds offset to current elevation
- Allows incremental elevation changes

### 5. **Configuration System**
Located in [handler_config.py](addon/globalPlugins/touchpoint/handler_config.py)

Provides declarative configuration for handlers:

```python
objectHandlerList = [
    GraphicHandler(effects={
        'enter': ComboEffect([VibrationEffect(...), LogEffect(...)]),
        'leave': ComboEffect([GlobalElevationEffect(0), VibrationEffect(...)])
    })
]

globalHandlerList = [
    ScreenBorderHandler(effects={
        'border_enter': ComboEffect([VibrationEffect(...)]),
        'border_leave': ComboEffect([VibrationEffect(0, 0, 0)])
    })
]
```

## Event Flow

### NVDA Object Events Flow

```
1. NVDA detects UI event (e.g., focus change)
   ↓
2. GlobalPlugin.event_<eventName>(obj, nextHandler)
   ↓
3. For each ObjectHandler in objectHandlers.handlers:
   ↓
4. Check if handler.filter.matches(obj)
   ↓
5. If match: handler.handle_event(event_name, obj, **kwargs)
   ↓
6. Look up effect in handler.effects[event_name]
   ↓
7. Execute effect(handler, obj, **kwargs)
   ↓
8. Effect sends commands to hardware driver
   ↓
9. Call nextHandler() to continue NVDA's event chain
```

**NVDA Events Handled:**
- `event_gainFocus` - Object receives keyboard focus
- `event_loseFocus` - Object loses keyboard focus  
- `event_foreground` - Window comes to foreground
- `event_nameChange` - Object name changes
- `event_valueChange` - Object value changes (sliders, inputs)
- `event_stateChange` - Object state changes (checkboxes, buttons)
- `event_selection` - Selection made in object
- `event_mouseMove` - Mouse movement detected

### Mouse Tracking Event Flow

The event tracking thread runs independently:

```
1. _event_tracking_thread runs in background loop (10ms interval)
   ↓
2. Get current mouse position via winUser.getCursorPos()
   ↓
3. Update plugin.mouse_position (thread-safe with lock)
   ↓
4. Get NVDA object under cursor: NVDAObjects.NVDAObject.objectFromPoint(x, y)
   ↓
5. Generate unique object ID from (windowHandle, IAccessibleChildID, name, role)
   ↓
6. Compare to previous object ID
   ↓
7. If changed:
   - Trigger 'leave' event for previous object's matching handlers
   - Trigger 'enter' event for new object's matching handlers
   ↓
8. Update curr_obj and curr_obj_id (thread-safe with lock)
   ↓
9. Run all GlobalHandlers:
   - Check if handler.filter.matches()
   - If match: call handler() to execute custom logic
   ↓
10. Handler may call trigger_event() to fire effects
```

### Screen Capture Flow (for GraphicHandler)

```
1. Mouse enters graphic object
   ↓
2. ObjectHandler.handle_event('enter', obj) called
   ↓
3. GraphicHandler adds capture region: plugin.add_capture_region(self, obj.location)
   ↓
4. _screen_capture_thread continuously captures registered regions
   ↓
5. For each region: _capture_screen_region() using mss library
   ↓
6. Call handler.capture_callback(region, image)
   ↓
7. GraphicHandler.capture_callback processes image:
   - Convert to grayscale
   - Apply Gaussian blur
   - Normalize to 0-1 range
   - Calculate mouse position in depth map
   - Extract elevation value at mouse position
   ↓
8. Send elevation to hardware: plugin.hardware.send_elevation(elevation)
   ↓
9. Update emulator GUI: plugin.hardware.update_depth_map(...)
   ↓
10. When mouse leaves: plugin.remove_capture_region(self)
```

## Threading Architecture

The system uses three main threads:

### 1. **Main NVDA Thread**
- Handles NVDA event callbacks
- Routes events to handlers
- Must not block (calls `nextHandler()`)

### 2. **Event Tracking Thread** (`_event_tracking_thread`)
- Polls mouse position at 10ms intervals
- Detects object changes under cursor
- Triggers enter/leave events
- Executes global handlers
- Thread-safe with locks for shared state

### 3. **Screen Capture Thread** (`_screen_capture_thread`)
- Creates mss instance (thread-local storage)
- Captures registered screen regions
- Calls handler callbacks with images
- Manages capture frequency (10ms active, 50ms idle)
- Thread-safe with locks for capture_regions dict

## Thread Safety

The architecture uses multiple locks to ensure thread safety:

- `curr_obj_lock` - Protects current object state
- `mouse_position_lock` - Protects mouse position cache
- `capture_regions_lock` - Protects screen capture registry
- `depth_map_lock` - Protects depth map data

## Hardware Integration

Event handlers ultimately send commands to the hardware driver:

- **`send_elevation(value)`** - Set tactile elevation
- **`send_vibration(amplitude, frequency, duration)`** - Trigger vibration
- **`add_elevation_offset(offset)`** - Adjust relative elevation

The hardware driver queues commands and communicates with the Touchpoint device over UART/UDP using the Songbird protocol.

## Extensibility

To add new event handling behavior:

1. **Create a Filter** (if needed) - Define matching criteria
2. **Create an Effect** - Implement the action to take
3. **Create a Handler** (if needed) - For specialized processing
4. **Configure in handler_config.py** - Map events to effects
5. **Add to handler list** - `objectHandlerList` or `globalHandlerList`

## Example: Adding a Button Click Handler

```python
# In filters.py
class ButtonFilter(ObjectFilter):
    def matches(self, plugin, obj):
        return obj.role == controlTypes.Role.BUTTON

# In effects.py  
class ClickVibrationEffect(Effect):
    def __call__(self, handler, obj=None, **kwargs):
        handler.plugin.hardware.send_vibration(0.8, 200.0, 50)

# In handler_config.py
from .filters import ButtonFilter
from .effects import ClickVibrationEffect

objectHandlerList.append(
    ObjectHandler(
        filter=ButtonFilter(),
        effects={
            'gainFocus': ClickVibrationEffect()
        }
    )
)
```
