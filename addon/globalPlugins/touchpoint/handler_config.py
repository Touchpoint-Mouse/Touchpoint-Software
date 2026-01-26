from .utils import logMessage
from .effects import ComboEffect, GlobalElevationEffect, VibrationEffect
from .handlers import GraphicHandler, ScreenBorderHandler

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