class Effect:
    """Base class for event effects."""
    
    def __call__(self, handler, obj=None, **kwargs):
        """Execute the effect.
        
        Args:
            handler: The handler object that owns this effect
            obj: The NVDA object (for ObjectHandler events) or None (for GlobalHandler events)
            **kwargs: Additional event-specific parameters
        """
        raise NotImplementedError("Effect subclasses must implement __call__")

class ComboEffect(Effect):
    """Class to combine multiple Effect objects."""
    
    def __init__(self, effects=[]):
        self.effects = effects
        
    def __call__(self, handler, obj=None, **kwargs):
        """Execute all combined effects."""
        for effect in self.effects:
            effect(handler, obj, **kwargs)
    
class VibrationEffect(Effect):
    """Effect to send vibration effect IDs using the new hardware protocol."""
    
    def __init__(self, effect_ids=None, priority=1):
        """
        Initialize the VibrationEffect.
        
        Args:
            effect_ids (list[int]): New protocol effect IDs to trigger
            priority (int): New protocol effect priority byte
        """
        self.effect_ids = [int(max(0, min(255, round(effect_id)))) for effect_id in (effect_ids or [])]
        self.priority = int(max(0, min(255, priority)))
        
    def __call__(self, handler, obj=None, **kwargs):
        """Execute the vibration effect ID command."""
        if self.effect_ids:
            handler.plugin.hardware.send_vibration_effects(self.priority, self.effect_ids)


class VibrationIntensityEffect(Effect):
    """Effect to send vibration intensity using the new hardware protocol."""

    def __init__(self, intensity=0, priority=1):
        """Initialize the VibrationIntensityEffect."""
        self.intensity = int(max(0, min(255, round(intensity))))
        self.priority = int(max(0, min(255, priority)))

    def __call__(self, handler, obj=None, **kwargs):
        """Execute the vibration intensity command."""
        handler.plugin.hardware.send_vibration_intensity(self.priority, self.intensity)

class GlobalElevationEffect(Effect):
    """ Effect to set the global elevation of the Touchpoint device. """
    def __init__(self, elevation=0.0, priority=0):
        """
        Initialize the GlobalElevationEffect.
        
        Args:
            elevation (float): Elevation value to set
            priority (int): Priority level for absolute elevation setting (0 = highest)
        """
        self.elevation = elevation
        self.priority = priority
        
    def __call__(self, handler, obj=None, **kwargs):
        """Execute the global elevation effect."""
        handler.plugin.hardware.set_global_elevation(self.elevation, priority=self.priority)
        
class RelativeElevationEffect(Effect):
    """ Effect to set the relative elevation of the Touchpoint device. Overidden by absolute elevation effects. """
    def __init__(self, offset=0.0):
        """
        Initialize the RelativeElevationEffect.
        
        Args:
            offset (float): Elevation offset to add
        """
        self.offset = offset
        
    def __call__(self, handler, obj=None, **kwargs):
        """Execute the relative elevation effect."""
        handler.plugin.hardware.add_elevation_offset(self.offset)