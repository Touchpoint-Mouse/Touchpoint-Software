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
The runtime has four cooperating subsystems:

1. Plugin orchestration
- GlobalPlugin owns lifecycle, threads, region updates, and dispatch order.

2. Render pipeline
- Layer graph (capture, object, depth, texture).
- Renderer graph (capture -> graphic -> texture vibration -> elevation).

3. Event/handler pipeline
- Object handlers and global handlers trigger explicit effects.

4. Hardware interface
- Songbird UART transport.
- Prioritized command emission for elevation and vibration.

## Threading Model
### NVDA event thread
- Receives NVDA object events.
- Forwards object events into ObjectHandlerManager.

### Render thread
- Implemented in GlobalPlugin._render_thread.
- Responsibilities per loop:
  1. Read cursor position.
  2. Dispatch mouse enter/leave transitions on object handlers.
  3. Compute new mouse-centered capture region.
  4. Update all layer region bounds.
  5. Execute renderers in configured order.
  6. Push images to emulator if open.
  7. Cycle layer state.
  8. Dispatch global handlers.
  9. Cycle hardware state machine.

### Capture camera thread
- Owned by CaptureRenderer.
- Continuously captures a larger screen buffer using capture scale factor.
- Render step crops from this buffer into the exact requested region.

### Hardware health thread
- Owned by HardwareDriver.
- Periodically pings device and updates connection status.

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

## Render Layers
## capture layer
- Dynamic size following capture region.
- BGR image.

## object layer
- Dynamic semantic/object label map.
- Maintains label hierarchy and depth allocation.

## depth layer
- Float32 grayscale elevation map.
- Constant size from config layer_dimensions['depth'].

## texture layer
- UInt8 single-channel edge texture.
- Constant size aligned to depth layer resolution.

## Renderer Graph
Current execution order:
1. CaptureRenderer
2. GraphicRenderer
3. TextureVibrationRenderer
4. ElevationRenderer

ObjectRenderer, DepthRenderer, and ObjectDepthRenderer exist but are currently not in the active execution list.

## CaptureRenderer
- Uses a scale-factor-expanded capture rect around the current region.
- Crops intersection into exact region dimensions every frame.
- Provides stable, centered context for downstream processing.

## GraphicRenderer
Inputs:
- capture_layer
- depth_layer
- texture_layer
- current mouse object filtered by GraphicFilter

Pipeline:
1. Validate active graphic target under mouse.
2. Convert capture BGR to grayscale float map.
3. Build working grid corresponding to capture/depth relationship.
4. Optional smoothing with scale-aware kernel.
5. Optional polarity decision (currently configurable and default-disabled).
6. Normalize to 0..1 and optional invert.
7. Edge extraction (Canny + optional dilation) and edge accentuation into depth.
8. Scale by max elevation and elevation_scale.
9. Mask output outside hovered object bbox.
10. Crop to hardware-equivalent center region:
- crop size = work_size / capture_scale_factor
11. Resize cropped region to fixed depth resolution.
12. Write:
- depth map to depth layer
- edge map to texture layer

Behavioral guarantees:
- Depth resolution is fixed by config.
- Capture scale increases context window, not final depth map resolution.

## TextureVibrationRenderer
Purpose:
- Convert edge activity in texture layer into real-time vibration intensity.

Inputs:
- texture_layer image at depth resolution.
- mouse movement state from plugin.get_mouse_position().

Behavior:
- If mouse has not moved since last frame:
  - sends vibration intensity 0 (off command).
- If texture is empty:
  - sends vibration intensity 0.
- If moving with valid texture:
  - computes activity from weighted p90 + mean edge intensity.
  - applies intensity_scale and min/max clamp.
  - applies exponential smoothing (smoothing_alpha).
  - sends send_vibration_intensity(priority, intensity).

## ElevationRenderer
- Reads center pixel of depth layer.
- Sends set_global_elevation(center_value, priority).

## Event and Effect System
Object handlers and global handlers remain active alongside renderers.

### ObjectHandler usage
- Graphic enter/leave events with vibration effects and elevation reset behavior.
- Per-handler entered-object state avoids oscillation.

### GlobalHandler usage
- Screen border enter/leave vibration intensity commands.

Effects available include:
- VibrationEffect (effect IDs)
- VibrationIntensityEffect (priority + scalar intensity)
- GlobalElevationEffect
- ComboEffect

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

## Important Invariants
1. Depth resolution invariant
- Depth map pixel resolution is controlled by hardware display resolution and depth layer multiplier only.
- Runtime ppm changes do not redefine depth layer resolution.

2. Capture scaling invariant
- capture_region.scale_factor controls capture window size only.
- Scale factor increases context, not final depth output dimensions.

3. Texture-depth alignment
- Texture layer resolution equals depth layer resolution.
- Edge texture and depth map are spatially aligned.

4. Stationary mouse vibration behavior
- Texture-based vibration sends intensity off commands when cursor is not moving.

## Dataflow Summary
1. Capture thread fills a larger scaled capture buffer.
2. Render thread crops exact region and runs renderers.
3. GraphicRenderer produces depth map and edge texture.
4. TextureVibrationRenderer emits vibration intensity from edge energy.
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

Example A (event-driven):
- Goal: play a short vibration effect whenever entering a graphic object.

```python
# render_pipeline.py (inside _create_handlers)
from .effects import ComboEffect, VibrationEffect
from .handlers import ObjectHandler
from .filters import GraphicFilter

self.object_handlers.append(
  ObjectHandler(
    filter=GraphicFilter(),
    effects={
      'enter': ComboEffect([
        VibrationEffect(effect_ids=[7], priority=40),
      ]),
      'leave': ComboEffect([
        VibrationEffect(effect_ids=[8], priority=40),
      ]),
    },
  )
)
```

Example B (frame-driven):
- Goal: emit stronger vibration when texture activity is high while moving.
- Implementation path: extend TextureVibrationRenderer with alternate activity metric
  (for example p95-only, Sobel energy, or thresholded edge density), then expose
  a renderer config key such as "activity_mode": "p95".

## Operational Notes
- This addon expects NVDA runtime modules and platform-specific integrations.
- Static analysis outside NVDA may report unresolved imports for NVDA/wx/songbird modules.
- Those environment-specific diagnostics are expected in non-NVDA development contexts.
