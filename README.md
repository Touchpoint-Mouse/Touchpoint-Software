# Haptic Rendering Architecture

## Purpose
This document describes the current Touchpoint haptic rendering architecture as implemented in the NVDA addon codebase.

The system converts:
- Mouse-centered screen capture
- Object semantics from NVDA
- Edge and depth processing

into real-time hardware output:
- Elevation commands
- Vibration effect commands
- Vibration intensity commands

It also mirrors the full pipeline in the emulator GUI.

## Source of Truth
Primary implementation files:
- addon/globalPlugins/touchpoint/touchpoint.py
- addon/globalPlugins/touchpoint/render_pipeline.py
- addon/globalPlugins/touchpoint/render_layers.py
- addon/globalPlugins/touchpoint/handlers.py
- addon/globalPlugins/touchpoint/effects.py
- addon/globalPlugins/touchpoint/hardware_driver.py
- addon/globalPlugins/touchpoint/config.py
- addon/globalPlugins/touchpoint/software_config.json
- addon/globalPlugins/touchpoint/hardware_config.json

## High-Level Architecture
The runtime is built from reusable class families rather than a single monolithic pipeline.

1. Orchestration classes
- GlobalPlugin owns lifecycle and threads.
- RenderPipeline composes layers, renderers, handlers, and managers.

2. Data classes
- RenderLayer is the base image/region container.
- ObjectLayer extends semantic labeling behavior on top of RenderLayer.

3. Behavior classes
- Renderer subclasses transform layers into new layer state or hardware commands.
- Filter subclasses decide whether handlers should match.
- Effect subclasses define what command behavior happens when events fire.

4. Transport/config classes
- HardwareDriver handles command arbitration and protocol I/O.
- TouchpointConfig computes dimensions and exposes config-derived parameters.

## Threading Model
Execution is distributed across class-owned threads:
- GlobalPlugin render thread: drives per-frame orchestration and calls RenderPipeline.
- CaptureRenderer camera thread: continuously acquires capture buffers.
- HardwareDriver health thread: monitors hardware connection/ping state.
- NVDA event thread: forwards object events into ObjectHandlerManager.

This keeps event detection, frame rendering, and hardware health isolated while sharing state via plugin/pipeline objects.

## Core Class Structure
### Filters
Filters answer: should this handler apply here?

- ObjectFilter: base class with matches(plugin, obj).
- ComboObjectFilter: include/exclude composition for object filters.
- GraphicFilter: built-in object filter for image/graphic-like targets.
- GlobalFilter: base class with matches(plugin).
- ComboGlobalFilter: include/exclude composition for global filters.

Typical usage: ObjectHandler(filter=SomeObjectFilter(), effects={...}).

### Effects
Effects answer: what action should happen when an event fires?

- Effect: base callable protocol (__call__(handler, obj=None, **kwargs)).
- ComboEffect: executes multiple effects in sequence.
- VibrationEffect: sends protocol vibration effect IDs.
- VibrationIntensityEffect: sends direct vibration intensity values.
- GlobalElevationEffect: sets absolute elevation with priority.
- RelativeElevationEffect: adds elevation offset.

Effects are transport-agnostic at call sites: handlers invoke effects, and effects route commands through handler.plugin.hardware.

### Handlers and Managers
Handlers bind events to effects; managers dispatch handlers.

- ObjectHandler: owns object filter + event-to-effect mapping.
- GlobalHandler: owns global filter + trigger_event API.
- ScreenBorderHandler: concrete global handler for border enter/leave.
- ObjectHandlerManager: event fan-out and mouse transition dispatch.
- GlobalHandlerManager: per-frame global handler dispatch.

Key pattern: filters decide applicability, then effects produce haptic behavior.

### Layers
Layers store frame data and region state.

- RenderLayer: base image container with current/previous image and region.
- SemanticLayer: semantic label-oriented layer base.
- ObjectLayer: semantic labels, label map, and depth allocation helpers.

Current layers in this project:
- capture layer: dynamic BGR screen image in the current capture region.
- object layer: dynamic semantic label map of detected/queried UI objects.
- depth layer: fixed-size float32 elevation map.
- texture layer: fixed-size uint8 edge/texture map aligned to depth.

### Renderers
Renderers consume layers (and plugin state) and produce new layer/hardware output.

- Renderer: base class with initialize(), __call__(), set_plugin().
- CaptureRenderer: refreshes capture layer from camera-thread buffers.
- ObjectRenderer: populates object layer from sampled object queries.
- DepthRenderer: generic depth-from-capture path.
- GraphicRenderer: graphic-focused depth + edge extraction path.
- TextureVibrationRenderer: texture activity to vibration intensity.
- ObjectDepthRenderer: object-semantic depth composition path.
- ElevationRenderer: converts depth center sample to elevation command.

Current active renderer order:
1. CaptureRenderer
2. GraphicRenderer
3. TextureVibrationRenderer
4. ElevationRenderer

Current renderer blurbs:
- CaptureRenderer: provides centered capture data each frame.
- GraphicRenderer: writes depth and texture layers from graphic targets.
- TextureVibrationRenderer: maps texture activity to vibration intensity, with stationary off behavior.
- ElevationRenderer: maps center depth to global elevation.

### Orchestration and Transport
- RenderPipeline: composition root for layers/renderers/handlers and frame execution order.
- GlobalPlugin: owns lifecycle, shared state, render loop, and event hookups.
- HardwareDriver: command gating, priority handling, UART packet emission, emulator mirroring.
- TouchpointConfig: computes derived dimensions and exposes runtime configuration.

## Configuration Model
## Hardware config
From hardware_config.json:
- headers: protocol headers for ping, elevation, vibration effect/intensity, pixels_per_mm.
- display:
  - resolution: base hardware sampling resolution scalar.
  - width_mm, height_mm: physical display dimensions.
  - initial_pixels_per_mm: startup scaling before telemetry.
- command_enable flags:
  - elevation
  - vibration_effect
  - vibration_intensity
  - dynamic_capture_resize

## Software config
From software_config.json:
- capture_region.scale_factor
- layer_multipliers (depth, texture, object_mesh)
- threading delays
- renderer-specific parameter blocks

## Derived dimensions
Computed in TouchpointConfig:
- Display aspect ratio prefers width_mm / height_mm.
- Layer dimensions use:
  - layer_area = display.resolution * layer_multiplier
  - height = sqrt(layer_area / aspect_ratio)
  - width = height * aspect_ratio
- Capture dimensions use direct physical scaling:
  - capture_w = ceil(width_mm * initial_pixels_per_mm * capture_scale_factor)
  - capture_h = ceil(height_mm * initial_pixels_per_mm * capture_scale_factor)

## Dynamic pixels_per_mm resize
When enabled and a pixels_per_mm packet arrives:
- Hardware dimensions:
  - hardware_w = ceil(width_mm * ppm)
  - hardware_h = ceil(height_mm * ppm)
- Capture dimensions:
  - capture_w = ceil(width_mm * ppm * capture_scale_factor)
  - capture_h = ceil(height_mm * ppm * capture_scale_factor)
- Plugin updates capture region size.
- Depth resolution does not change dynamically.

## Event and Effect System
Event processing is intentionally class-driven:

1. Source event enters a manager.
- NVDA object events go to ObjectHandlerManager.dispatch_event(...).
- Global periodic checks run through GlobalHandlerManager.dispatch_events().

2. Handler matching happens through a filter.
- ObjectHandler calls filter.matches(plugin, obj).
- GlobalHandler calls filter.matches(plugin).

3. Effect execution happens through event keys.
- Handler maps event names (enter, leave, gainFocus, border_enter, etc.) to Effect instances.
- Effect __call__ implementations send commands via HardwareDriver.

4. Mouse transition support is built into ObjectHandlerManager.
- Per-handler entered-object state and bbox exit checks reduce enter/leave oscillation.

This separation is the main extension mechanism: new behavior usually means adding a filter, an effect, or both.

## Hardware Command Model
### Elevation
- set_global_elevation() stores highest-priority command for cycle.
- cycle_state() sends effective elevation packet.

### Vibration effect
- send_vibration_effects(priority, [effect_ids...])
- gated by command_enable.vibration_effect

### Vibration intensity
- send_vibration_intensity(priority, intensity)
- gated by command_enable.vibration_intensity
- clamped by vibration.max_intensity

### Emulator synchronization
All outgoing commands update emulator GUI state/logs even when hardware is disconnected.

## Dataflow Summary
1. Capture thread fills a larger scaled capture buffer.
2. Render thread processes hardware and event states and runs renderers.
3. GraphicRenderer produces depth map and edge texture from images.
4. TextureVibrationRenderer emits vibration intensity from edge energy on images.
5. ElevationRenderer emits center-pixel elevation.
6. Handler system can emit additional event-driven effects.
7. Hardware driver arbitrates and transmits commands.
8. Emulator mirrors layers and command activity.

## Extension Points
### Add a new renderer
1. Implement Renderer subclass in render_layers.py.
2. Add creation in RenderPipeline._create_renderers().
3. Insert into RenderPipeline.get_renderers() order.
4. Add renderer config block under software_config.json: renderers.<name>.

Example:
- Goal: add a simple center-pulse renderer that emits vibration intensity based on center depth.

```python
# render_layers.py
class CenterPulseRenderer(Renderer):
  def __init__(self, depth_layer, priority=30, scale=127.0):
    super().__init__()
    self.depth_layer = depth_layer
    self.priority = priority
    self.scale = float(scale)

  def __call__(self):
    depth = self.depth_layer.get_image()
    if depth is None or depth.size == 0:
      return
    h, w = depth.shape[:2]
    center_value = float(depth[h // 2, w // 2])
    intensity = int(max(0, min(255, round(center_value * self.scale))))
    self.plugin.hardware.send_vibration_intensity(self.priority, intensity)
```

```python
# render_pipeline.py (inside _create_renderers)
self.center_pulse_renderer = self._create_renderer_instance(
  "center_pulse_renderer",
  CenterPulseRenderer,
  self.depth_layer,
)
```

```python
# render_pipeline.py (inside get_renderers)
return [
  self.capture_renderer,
  self.graphic_renderer,
  self.center_pulse_renderer,
  self.texture_vibration_renderer,
  self.elevation_renderer,
]
```

```json
// software_config.json
"renderers": {
  "center_pulse_renderer": {
    "priority": 30,
    "scale": 127.0
  }
}
```

### Add a new haptic behavior
1. Add an Effect subclass if event-driven.
2. Or add a renderer if frame-driven.
3. Route commands through HardwareDriver methods.
4. Tune via software/hardware config parameters.

Example:
- Goal: encode button size as a haptic cue (small/medium/large) on enter.

```python
# filters.py
import controlTypes

class ButtonFilter(ObjectFilter):
  """Match only button-like controls."""
  BUTTON_ROLES = {
    controlTypes.Role.BUTTON,
    controlTypes.Role.TOGGLEBUTTON,
    controlTypes.Role.SPLITBUTTON,
  }

  def matches(self, plugin, obj):
    if not obj:
      return False
    role = getattr(obj, 'role', None)
    return role in self.BUTTON_ROLES
```

```python
# effects.py
class ObjectSizeCueEffect(Effect):
  def __init__(self, priority=50, small_id=3, medium_id=5, large_id=9):
    self.priority = int(max(0, min(255, priority)))
    self.small_id = small_id
    self.medium_id = medium_id
    self.large_id = large_id

  def __call__(self, handler, obj=None, **kwargs):
    if obj is None or getattr(obj, 'location', None) is None:
      return

    area = max(1, int(obj.location.width) * int(obj.location.height))
    if area < 20_000:
      effect_id = self.small_id
      intensity = 60
    elif area < 120_000:
      effect_id = self.medium_id
      intensity = 110
    else:
      effect_id = self.large_id
      intensity = 170

    handler.plugin.hardware.send_vibration_effects(self.priority, [effect_id])
    handler.plugin.hardware.send_vibration_intensity(self.priority, intensity)
```

```python
# render_pipeline.py (inside _create_handlers)
from .effects import ComboEffect, ObjectSizeCueEffect, VibrationIntensityEffect
from .handlers import ObjectHandler
from .filters import ButtonFilter

self.object_handlers.append(
  ObjectHandler(
    filter=ButtonFilter(),
    effects={
      'enter': ObjectSizeCueEffect(priority=50),
      'leave': VibrationIntensityEffect(intensity=0, priority=50)
    },
  )
)
```

## Operational Notes
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
