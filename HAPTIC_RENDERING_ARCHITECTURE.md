# Haptic Rendering Architecture

## Overview

The Touchpoint NVDA addon implements a sophisticated haptic rendering pipeline that processes UI events, screen content, and mouse interactions to provide real-time haptic feedback through the Touchpoint hardware device. The architecture is built on a multi-layered design with three main subsystems:

1. **Event Handling System** - Detects and responds to UI events and mouse interactions
2. **Render Layer System** - Captures and processes screen content into haptic representations
3. **Hardware Integration** - Communicates with Touchpoint device for haptic output

This document covers all three subsystems and their interactions.

## Table of Contents

- [Core Components](#core-components)
  - GlobalPlugin, Handler System, Filter System, Effect System, Configuration, Render Layer System, Emulator GUI
- [Pipeline Architecture](#pipeline-architecture)
  - Render Pipeline Flow, Region Management, NVDA Events, Emulator Updates
- [Threading Architecture](#threading-architecture)
  - Main Thread, Render Thread, Camera Thread, GUI Thread
- [Thread Safety](#thread-safety)
  - Locks, Safe Operations
- [Hardware Integration](#hardware-integration)
  - Hardware Commands
- [Extensibility](#extensibility)
  - Adding Event Handlers, Render Layers, Hardware Output
- [Examples](#examples)
  - Button Click Handler, Edge Detection Layer, Dynamic Regions
- [Performance Considerations](#performance-considerations)

## Architecture Overview Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          NVDA Event Thread                          │
│  (UI Events: Focus, Value Changes, Mouse Moves, etc.)              │
└────────────────┬────────────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      Object & Global Handlers                       │
│  • Filter NVDA objects (buttons, graphics, documents)              │
│  • Execute effects (vibration, elevation, logging)                 │
│  • Trigger hardware commands                                       │
└─────────────────────────────────────────────────────────────────────┘

        ┌────────────────────────────────────────────┐
        │         Render Thread (100 Hz)            │
        │  ┌──────────────────────────────────────┐ │
        │  │  1. Update Mouse Position            │ │
        │  └──────────────────────────────────────┘ │
        │  ┌──────────────────────────────────────┐ │
        │  │  2. Execute Renderer Pipeline:       │ │
        │  │     - CaptureRenderer                │ │
        │  │     - DepthRenderer                  │ │
        │  │     - ElevationRenderer              │ │
        │  └──────────────────────────────────────┘ │
        │  ┌──────────────────────────────────────┐ │
        │  │  3. Update Emulator GUI              │ │
        │  └──────────────────────────────────────┘ │
        │  ┌──────────────────────────────────────┐ │
        │  │  4. Execute Global Handlers          │ │
        │  └──────────────────────────────────────┘ │
        │  ┌──────────────────────────────────────┐ │
        │  │  5. Cycle Hardware State Machine     │ │
        │  └──────────────────────────────────────┘ │
        └────────────────────────────────────────────┘
                         │
       ┌─────────────────┼─────────────────┐
       │                 │                 │
       ▼                 ▼                 ▼
┌─────────────┐   ┌──────────────┐  ┌──────────────────┐
│   Render    │   │   Hardware   │  │  Emulator GUI    │
│   Layers    │   │    Driver    │  │  (60 FPS)        │
│             │   │              │  │                  │
│ • Capture   │   │ • Elevation  │  │ • Layer Tabs     │
│ • Semantic  │   │ • Vibration  │  │ • Elevation      │
│ • Depth     │   │ • Commands   │  │ • Status         │
│ • Texture   │   │ • UART/UDP   │  │ • Log            │
└─────────────┘   └──────────────┘  └──────────────────┘
      │                  │
      │                  ▼
      │        ┌──────────────────┐
      │        │  Touchpoint      │
      │        │  Hardware        │
      │        │  (Physical)      │
      │        └──────────────────┘
      │
      └──────────> Camera Thread (Continuous)
                  Uses mss for screen capture
```

## Core Components

### 1. **GlobalPlugin** (Main Entry Point)
Located in [touchpoint.py](addon/globalPlugins/touchpoint/touchpoint.py)

The `GlobalPlugin` class is the central orchestrator that:
- Initializes the plugin when NVDA starts
- Manages handler collections (object and global handlers)
- Manages render layers and renderers
- Runs background render thread for continuous screen capture and processing
- Interfaces with hardware driver for haptic output
- Tracks mouse position and current UI object state
- Updates emulator GUI with rendered layer data

**Key Responsibilities:**
- Event routing to appropriate handlers
- Thread management (render thread at 10ms intervals)
- Mouse position tracking with thread-safe locks
- Render layer lifecycle management
- Renderer execution pipeline
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
Located in [render_config.py](addon/globalPlugins/touchpoint/render_config.py)

Provides declarative configuration for the entire rendering pipeline:

```python
# Create render layers
captureLayer = RenderLayer(id="capture", dtype=np.uint8)
depthLayer = RenderLayer(id="depth", dtype=np.uint8)
semanticLayer = RenderLayer(id="semantic", dtype=np.uint8)
textureLayer = RenderLayer(id="texture", dtype=np.bool)

renderLayerList = [captureLayer, semanticLayer, depthLayer, textureLayer]

# Create renderers
rendererList = [
    CaptureRenderer(captureLayer),  # Screen capture
    DepthRenderer(captureLayer, depthLayer),  # Depth processing
    ElevationRenderer(depthLayer)  # Hardware output
]

# Configure event handlers
objectHandlerList = [
    ObjectHandler(effects={
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

### 6. **Render Layer System**
Located in [render_layers.py](addon/globalPlugins/touchpoint/render_layers.py)

The render layer system provides a flexible pipeline for capturing, processing, and rendering screen content into haptic output.

#### **RenderLayer**
A thread-safe data container for rendered image data:
- **`id`** - Unique identifier (e.g., "capture", "depth", "semantic")
- **`image`** - NumPy array storing the rendered data
- **`image_lock`** - Threading lock for safe concurrent access
- **`get_image()`** - Returns thread-safe copy of current image
- **`update_image(new_image)`** - Updates image with thread safety
- **`update_region_size(region)`** - Resizes layer while preserving content
- **`get_screen_region()`** - Returns absolute screen coordinates for capture

**Predefined Layers:**
- **Capture Layer** - Raw BGR screen capture centered on mouse cursor
- **Semantic Layer** - Semantic segmentation (object/text identification)
- **Depth Layer** - Grayscale depth map for elevation rendering
- **Texture Layer** - Binary texture/edge detection map

#### **Renderer** (Base Class)
Base class for all rendering operations:
- **`initialize()`** - Optional setup method called during plugin initialization
- **`__call__()`** - Main execution method called each render cycle
- **`set_plugin(plugin)`** - Receives plugin reference for hardware/state access

#### **CaptureRenderer**
Captures screen region around mouse cursor:
- Runs dedicated camera thread using `mss` library for efficient screen capture
- Updates capture layer on every frame
- Centers region on current mouse position
- Handles screen boundary clamping
- Converts BGRA screenshots to BGR format

#### **DepthRenderer**
Processes capture layer into depth representation:
- Reads from capture layer
- Converts BGR to grayscale
- Normalizes brightness to 0-255 depth range
- Writes to depth layer
- (Can be extended with actual depth estimation algorithms)

#### **ElevationRenderer**
Converts depth data to hardware elevation commands:
- Reads center pixel from depth layer
- Sends elevation value to hardware driver
- Supports priority system for elevation overrides
- Non-blocking hardware communication

### 7. **Emulator GUI**
Located in [emulator_gui.py](addon/globalPlugins/touchpoint/emulator_gui.py)

Provides real-time visualization of the haptic rendering pipeline:
- **Layer Tabs** - Notebook control with tabs for each render layer
- **Layer Visualization** - Displays layer images with colormap application
- **Elevation Indicator** - Water-level display of current elevation
- **Hardware Status** - Shows connection state
- **Vibration Log** - Event log of all vibration commands

**Key Features:**
- **Dynamic Layer Population** - Tabs created automatically from layer IDs
- **Aspect Ratio Preservation** - Maintains capture region proportions
- **Real-time Updates** - 60 FPS refresh rate for smooth visualization
- **Thread-Safe Updates** - Layer images updated via `update_layer_image(layer_id, image)`

## Pipeline Architecture

### Render Pipeline Flow

The render thread executes continuously at 100 Hz (10ms intervals):

```
1. Render Thread Loop (_render_thread)
   ↓
2. Update mouse position: winUser.getCursorPos()
   ↓
3. Execute renderers in sequence:
   |
   ├─> CaptureRenderer:
   |     - Camera thread captures screen region centered on mouse
   |     - Updates captureLayer.image
   |
   ├─> DepthRenderer:
   |     - Read captureLayer.image
   |     - Convert to grayscale depth map
   |     - Updates depthLayer.image
   |
   └─> ElevationRenderer:
         - Read center pixel from depthLayer
         - Send elevation command to hardware
   ↓
4. Update Emulator GUI (if open):
   - For each layer: emulator_gui.update_layer_image(layer.id, layer.image)
   ↓
5. Execute Global Handlers:
   - globalHandlers.dispatch_events()
   - Check border detection, custom conditions
   ↓
6. Cycle Hardware State Machine:
   - hardware.cycle_state()
   - Process command queue, update device
   ↓
7. Sleep 10ms, repeat
```

### Region Management

Render regions are dynamically sized and centered on the mouse cursor:

```python
# Region definition
Region = namedtuple('Region', ['left', 'top', 'width', 'height'])

# Default configuration (100x100 pixels centered on cursor)
capture_region_width = 100
capture_region_height = 100

# Each layer calculates its screen region:
left = mouse_x - (width // 2)
top = mouse_y - (height // 2)
region = Region(left, top, width, height)
```

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

### Emulator GUI Update Flow

```
1. Render thread updates layer images:
   - layer.update_image(new_image) for each renderer output
   ↓
2. Check if emulator window is open:
   - emulator_gui.is_window_open()
   ↓
3. If open, send each layer to GUI:
   - emulator_gui.update_layer_image(layer.id, layer.image)
   ↓
4. GUI stores images in dictionary (thread-safe):
   - layer_images[layer_id] = image.copy()
   ↓
5. GUI refresh cycle (60 FPS):
   - Refresh currently selected layer tab
   - Apply colormap for visualization
   - Resize to fit panel with aspect ratio preservation
   - Draw cursor crosshair at center
   ↓
6. User can switch tabs to view different layers
```

### Emulator Initialization Flow

```
1. Plugin initialization (_initialize_async):
   ↓
2. Initialize emulator with render configuration:
   - layer_ids = [layer.id for layer in renderLayers]
   - aspect_ratio = capture_width / capture_height
   - emulator_gui.initialize_layers(layer_ids, aspect_ratio)
   ↓
3. User opens emulator (NVDA+Shift+E):
   - script_openEmulator() called
   - Re-initialize with latest layer info
   - emulator_gui.open_window(hardware_status)
   ↓
4. GUI creates window with notebook tabs:
   - _populate_layer_tabs() creates tab for each layer
   - Binds paint events for each layer panel
   - Starts 60 FPS update timer
```

## Threading Architecture

The system uses multiple threads for parallel processing:

### 1. **Main NVDA Thread**
- Handles NVDA event callbacks
- Routes events to handlers
- Must not block (calls `nextHandler()`)
- Executes event handler effects

### 2. **Render Thread** (`_render_thread`)
- Polls mouse position at 10ms intervals (100 Hz)
- Executes renderer pipeline in sequence
- Updates render layer images
- Sends layer updates to emulator GUI
- Executes global handlers
- Cycles hardware state machine
- Thread-safe with locks for shared state

### 3. **Camera Thread** (within CaptureRenderer)
- Dedicated thread for screen capture
- Uses `mss` library for efficient capture
- Continuously captures region around mouse cursor
- Updates capture layer with thread-safe locks
- Runs independently from render thread for maximum capture rate

### 4. **GUI Thread** (wx main thread)
- Manages emulator window events
- Handles layer tab switching
- Renders layer visualizations at 60 FPS
- Updates elevation indicator
- Logs vibration events
- Communicates with render thread via thread-safe layer image dictionary

## Thread Safety

The architecture uses multiple locks to ensure thread safety:

### Plugin-Level Locks
- **`mouse_position_lock`** - Protects mouse position cache (updated by render thread)
- **`curr_obj_lock`** - Protects current NVDA object state

### Layer-Level Locks
- **`RenderLayer.image_lock`** - Protects each layer's image data
  - Locks when reading: `get_image()` returns safe copy
  - Locks when writing: `update_image()` safely updates
  - Prevents race conditions between renderers and GUI

### GUI-Level Locks
- **`layer_images_lock`** - Protects emulator's layer image dictionary
  - Render thread writes layer updates
  - GUI thread reads for display
- **`depth_map_lock`** - Legacy lock (to be removed)

### Thread-Safe Operations

```python
# Safe layer update (renderer)
with self.layer.image_lock:
    self.layer.image = new_image.copy()

# Safe layer read (another renderer)
capture_img = self.capture_layer.get_image()  # Returns locked copy

# Safe GUI update
with self.layer_images_lock:
    self.layer_images[layer_id] = image.copy()
```

## Hardware Integration

Event handlers ultimately send commands to the hardware driver:

- **`send_elevation(value)`** - Set tactile elevation
- **`send_vibration(amplitude, frequency, duration)`** - Trigger vibration
- **`add_elevation_offset(offset)`** - Adjust relative elevation

The hardware driver queues commands and communicates with the Touchpoint device over UART/UDP using the Songbird protocol.

## Extensibility

### Adding New Event Handling Behavior

1. **Create a Filter** (if needed) - Define matching criteria
2. **Create an Effect** - Implement the action to take
3. **Create a Handler** (if needed) - For specialized processing
4. **Configure in render_config.py** - Map events to effects
5. **Add to handler list** - `objectHandlerList` or `globalHandlerList`

### Adding New Render Layers

1. **Create RenderLayer** in `render_config.py`:
   ```python
   myLayer = RenderLayer(id="myLayer", dtype=np.uint8)
   renderLayerList.append(myLayer)
   ```

2. **Create Renderer** class in `render_layers.py`:
   ```python
   class MyRenderer(Renderer):
       def __init__(self, input_layer, output_layer):
           self.input_layer = input_layer
           self.output_layer = output_layer
       
       def __call__(self):
           # Read from input
           input_img = self.input_layer.get_image()
           # Process
           output_img = process(input_img)
           # Write to output
           self.output_layer.update_image(output_img)
   ```

3. **Add to renderer list** in `render_config.py`:
   ```python
   rendererList.append(MyRenderer(captureLayer, myLayer))
   ```

4. **Result**: New layer automatically appears as tab in emulator GUI

### Adding Hardware Output Renderers

1. **Create renderer** that reads from layer and sends to hardware:
   ```python
   class VibrationRenderer(Renderer):
       def __init__(self, texture_layer):
           self.texture_layer = texture_layer
       
       def __call__(self):
           texture_img = self.texture_layer.get_image()
           # Analyze texture intensity
           intensity = calculate_texture_intensity(texture_img)
           # Send to hardware
           if intensity > threshold:
               self.plugin.hardware.send_vibration(
                   amplitude=intensity,
                   frequency=200.0,
                   duration=0  # Continuous
               )
   ```

2. **Add to renderer list** - executes every render cycle

### Rendering Pipeline Customization

The renderer execution order matters:

```python
rendererList = [
    CaptureRenderer(captureLayer),      # 1. Capture screen
    SemanticRenderer(captureLayer, semanticLayer),  # 2. Identify objects
    DepthRenderer(captureLayer, depthLayer),        # 3. Compute depth
    TextureRenderer(captureLayer, textureLayer),    # 4. Detect edges
    ElevationRenderer(depthLayer),      # 5. Send elevation to hardware
    VibrationRenderer(textureLayer)     # 6. Send vibration to hardware
]
```

Each renderer runs sequentially in the render thread.

## Examples

### Example 1: Adding a Button Click Handler

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

### Example 2: Adding an Edge Detection Render Layer

This example creates a complete render layer for edge detection with haptic output:

```python
# In render_layers.py - Add new renderer class
class EdgeRenderer(Renderer):
    """Renderer that detects edges in capture layer and writes to texture layer."""
    
    def __init__(self, capture_layer, texture_layer, threshold=100):
        self.capture_layer = capture_layer
        self.texture_layer = texture_layer
        self.threshold = threshold
        
    def __call__(self):
        """Compute edges using Canny edge detection."""
        try:
            # Read capture image
            capture_img = self.capture_layer.get_image()
            if capture_img.size == 0:
                return
            
            # Convert to grayscale
            gray = cv2.cvtColor(capture_img, cv2.COLOR_BGR2GRAY)
            
            # Apply Gaussian blur to reduce noise
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            
            # Canny edge detection
            edges = cv2.Canny(blurred, self.threshold, self.threshold * 2)
            
            # Write to texture layer (binary format)
            self.texture_layer.update_image(edges)
            
        except Exception as e:
            logMessage(f"[ERROR] EdgeRenderer failed: {e}")

class TextureVibrationRenderer(Renderer):
    """Renderer that triggers vibration based on texture intensity."""
    
    def __init__(self, texture_layer, sensitivity=0.3):
        self.texture_layer = texture_layer
        self.sensitivity = sensitivity
        self.last_intensity = 0.0
        
    def __call__(self):
        """Calculate texture intensity and send vibration."""
        try:
            # Read texture image
            texture_img = self.texture_layer.get_image()
            if texture_img.size == 0:
                return
            
            # Get center region (10x10 pixels around cursor)
            height, width = texture_img.shape[:2]
            cy, cx = height // 2, width // 2
            center_region = texture_img[cy-5:cy+5, cx-5:cx+5]
            
            # Calculate edge density (0.0 to 1.0)
            intensity = np.mean(center_region) / 255.0
            
            # Smooth intensity changes
            intensity = 0.7 * self.last_intensity + 0.3 * intensity
            self.last_intensity = intensity
            
            # Send vibration if above threshold
            if intensity > self.sensitivity:
                freq = 100.0 + (intensity * 150.0)  # 100-250 Hz
                self.plugin.hardware.send_vibration(
                    amplitude=intensity,
                    frequency=freq,
                    duration=0  # Continuous
                )
            else:
                # Stop vibration when no edges
                self.plugin.hardware.send_vibration(0, 0, 0)
                
        except Exception as e:
            logMessage(f"[ERROR] TextureVibrationRenderer failed: {e}")

# In render_config.py - Update configuration
textureLayer = RenderLayer(id="texture", dtype=np.uint8)  # Changed from bool to uint8

renderLayerList = [
    captureLayer,
    semanticLayer,
    depthLayer,
    textureLayer  # Now includes edge data
]

rendererList = [
    CaptureRenderer(captureLayer),              # 1. Capture screen
    DepthRenderer(captureLayer, depthLayer),    # 2. Compute depth
    EdgeRenderer(captureLayer, textureLayer),   # 3. Detect edges
    ElevationRenderer(depthLayer),              # 4. Elevation from depth
    TextureVibrationRenderer(textureLayer)      # 5. Vibration from edges
]
```

**Result**: The system now:
- Captures screen around cursor
- Computes both depth map and edge map
- Sends elevation to hardware based on depth
- Sends vibration to hardware based on edge density
- Displays all layers (including edges) as tabs in emulator GUI
- Users can switch to "Texture" tab to see edge visualization in real-time

### Example 3: Custom Region Size for Different Contexts

```python
# In touchpoint.py - Dynamic region sizing
def update_capture_region_size(self, width, height):
    """Update the capture region size for all layers."""
    new_region = Region(left=0, top=0, width=width, height=height)
    
    for layer in self.renderLayers:
        layer.update_region_size(new_region)
    
    # Update emulator aspect ratio
    aspect_ratio = width / height
    self.emulator_gui.initialize_layers(
        [layer.id for layer in self.renderLayers],
        aspect_ratio
    )
    
    logMessage(f"Updated capture region: {width}x{height}")

# Example usage - larger region for detailed content
def event_gainFocus(self, obj, nextHandler):
    if obj.role == controlTypes.Role.DOCUMENT:
        # Use larger region for documents
        self.update_capture_region_size(200, 200)
    else:
        # Use default smaller region
        self.update_capture_region_size(100, 100)
    
    nextHandler()
```

## Performance Considerations

### Render Thread Performance
- **Target**: 100 Hz (10ms per cycle)
- **Budget**: ~8ms for all renderers to maintain real-time performance
- Renderers should be optimized for speed
- Heavy processing should be moved to separate threads

### Camera Thread Performance
- Runs independently from render thread
- Screen capture typically takes 2-5ms
- Uses `mss` library optimized for low-latency capture

### GUI Performance
- 60 FPS (16ms per frame)
- Only redraws currently visible layer tab
- Image scaling uses `cv2.INTER_NEAREST` for speed

### Memory Management
- Layers use NumPy arrays for efficient memory usage
- Thread-safe copies made only when necessary
- Old layer data automatically garbage collected

### Optimization Tips
1. **Minimize Layer Copies**: Use `get_image()` only when needed
2. **Resize Carefully**: Process smaller regions when possible
3. **Skip Empty Frames**: Check `image.size == 0` before processing
4. **Use NumPy Operations**: Vectorized operations are much faster
5. **Profile Renderers**: Log execution time for slow renderers
