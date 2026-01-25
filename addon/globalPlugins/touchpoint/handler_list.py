from addon.globalPlugins.touchpoint.effects import VibrationEffect
from .handlers import GraphicHandler, ScreenBorderHandler

objectHandlerList = [
    GraphicHandler(effects={
        'enter': VibrationEffect(0.1, 180.0, 1),
        'leave': VibrationEffect(0.05, 80.0, 1),
    })
]

globalHandlerList = [
    ScreenBorderHandler(effects={
        'border_enter': VibrationEffect(0.1, 200.0, 0),
        'border_leave': VibrationEffect(0.05, 100.0, 0)
    })
]