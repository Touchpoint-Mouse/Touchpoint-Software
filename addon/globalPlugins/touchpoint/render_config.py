from .utils import logMessage
from .render_layers import (
    RenderLayer,
    CaptureRenderer,
    DepthRenderer,
    ElevationRenderer
)
from .effects import ComboEffect, GlobalElevationEffect, VibrationEffect
from .handlers import GraphicHandler, ScreenBorderHandler

# Create layer instances - all just data containers now
captureLayer = RenderLayer()
depthLayer = RenderLayer()
semanticLayer = RenderLayer()
textureLayer = RenderLayer()

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
    ElevationRenderer(depthLayer)  # Reads depth center pixel, sets elevation (plugin set via set_plugin())
]

objectHandlerList = [
    GraphicHandler(effects={
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