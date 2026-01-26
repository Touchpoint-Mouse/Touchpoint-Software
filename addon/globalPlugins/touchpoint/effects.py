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
    """Effect to send a vibration command to the Touchpoint device."""
    
    def __init__(self, amplitude=0.5, frequency=150.0, duration=500):
        """
        Initialize the VibrationEffect.
        
        Args:
            amplitude (float): Vibration amplitude (0.0 to 1.0)
            frequency (float): Vibration frequency in Hz
            duration (int): Vibration duration in milliseconds
        """
        self.amplitude = amplitude
        self.frequency = frequency
        self.duration = duration
        
    def __call__(self, handler, obj=None, **kwargs):
        """Execute the vibration effect."""
        handler.plugin.hardware.send_vibration(self.amplitude, self.frequency, self.duration)

class GlobalElevationEffect(Effect):
    """ Effect to set the global elevation of the Touchpoint device. (overrides relative elevation) """
    def __init__(self, elevation=0.0):
        """
        Initialize the GlobalElevationEffect.
        
        Args:
            elevation (float): Elevation value to set
        """
        self.elevation = elevation
        
    def __call__(self, handler, obj=None, **kwargs):
        """Execute the global elevation effect."""
        handler.plugin.hardware.send_elevation(self.elevation)
        
class RelativeElevationEffect(Effect):
    """ Effect to set the relative elevation of the Touchpoint device. """
    def __init__(self, offset=0.0):
        """
        Initialize the RelativeElevationEffect.
        
        Args:
            offset (float): Elevation offset to add
        """
        self.offset = offset
        
    def __call__(self, handler, obj=None, **kwargs):
        """Execute the global elevation effect."""
        handler.plugin.hardware.add_elevation_offset(self.offset)