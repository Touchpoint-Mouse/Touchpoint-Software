from .utils import logMessage
from .render_layers import (
    RenderLayer,
    CaptureRenderer,
    DepthRenderer,
    ElevationRenderer
)
from .effects import ComboEffect, GlobalElevationEffect, VibrationEffect
from .handlers import ObjectHandler, ScreenBorderHandler
from .dependencies import np

# Create layer instances - all just data containers now
captureLayer = RenderLayer(id="capture", dtype=np.uint8)  # Captured screen image (BGR format)
semanticLayer = RenderLayer(id="semantic", dtype=np.uint8)  # Semantic segmentation (object labels)
depthLayer = RenderLayer(id="depth", dtype=np.uint8) # Depth map (discretized depth values)
textureLayer = RenderLayer(id="texture", dtype=np.bool)  # Binary texture presence (e.g. edges)

# Render layer list
renderLayerList = [
    captureLayer,
    semanticLayer,
    depthLayer,
    textureLayer
]

rendererList = [
    CaptureRenderer(captureLayer),  # Captures screen to capture layer
    DepthRenderer(captureLayer, depthLayer),  # Converts capture to depth
    ElevationRenderer(depthLayer)  # Reads depth center pixel, sets elevation
]

objectHandlerList = [
    ObjectHandler(effects={
        'enter': ComboEffect([VibrationEffect(0.1, 180.0, 1), lambda effect, obj=None, **kwargs: logMessage(f"Mouse entered image: {obj.name if obj.name else 'Unnamed'} at {obj.location}")]),
        'leave': ComboEffect([GlobalElevationEffect(0), VibrationEffect(0.05, 80.0, 1), lambda effect, obj=None, **kwargs: logMessage(f"Mouse left image: {obj.name if obj.name else 'Unnamed'} at {obj.location}")])
    })
]

globalHandlerList = [
    ScreenBorderHandler(effects={
        'border_enter': ComboEffect([VibrationEffect(0.1, 200.0, 0), lambda effect, obj=None, **kwargs: logMessage("Mouse entered screen border")]),
        'border_leave': ComboEffect([VibrationEffect(0, 0, 0), lambda effect, obj=None, **kwargs: logMessage("Mouse left screen border")])
    })
]