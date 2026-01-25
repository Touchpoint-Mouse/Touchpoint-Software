import threading
from xml.sax import handler
from .filters import GlobalFilter, ObjectFilter, GraphicFilter
from .utils import logMessage
from .effects import Effect
import cv2
import numpy as np

class HandlerManager:
    """Class to manage multiple NVDA object handlers."""
    
    def __init__(self, plugin, handlers = []):
        self.plugin = plugin
        self.handlers = handlers
        
    def add_handler(self, handler):
        """Add a new handler to the manager."""
        self.handlers.append(handler)
        handler.set_plugin(self.plugin)
    
    def populate(self, handler_list):
        """Populate handlers from a given list."""
        for handler in handler_list:
            self.add_handler(handler)

class ObjectHandler:
    """Class to handle NVDA object-related events and interactions."""
    
    def __init__(self, filter=ObjectFilter(), effects=None):
        """Initialize the handler.
        
        Args:
            filter: Filter to determine which objects this handler applies to
            effects: Dict of event_name -> Effect object mappings
        """
        self.plugin = None
        self.filter = filter
        self.effects = effects or {}
        
    def set_plugin(self, plugin):
        """Set the parent plugin for this handler."""
        self.plugin = plugin
        
    def matches(self, obj):
        """Check if the given NVDA object matches the filter criteria.
        
        Args:
            obj: The NVDA object to check.
        """
        return self.filter.matches(self.plugin, obj)
    
    def handle_event(self, event_name, obj, **kwargs):
        """Handle an event by calling the appropriate effect.
        
        Args:
            event_name: String identifier for the event (e.g., 'enter', 'leave', 'gainFocus')
            obj: The NVDA object associated with the event
            **kwargs: Additional event-specific parameters
        """
        if event_name in self.effects:
            try:
                self.effects[event_name](self, obj, **kwargs)
            except Exception as e:
                logMessage(f"[ERROR] Effect '{event_name}' failed in {self.__class__.__name__}: {e}")
                import traceback
                logMessage(traceback.format_exc())
    
class GlobalHandler:
    """Class to handle global NVDA events."""
    
    def __init__(self, filter=GlobalFilter(), effects=None):
        """Initialize the handler.
        
        Args:
            filter: Filter to determine when this handler is active
            effects: Dict of event_name -> Effect object mappings
        """
        self.plugin = None
        self.filter = filter
        self.effects = effects or {}
    
    def set_plugin(self, plugin):
        """Set the parent plugin for this handler."""
        self.plugin = plugin
        
    def matches(self):
        """Check if the handler should be active."""
        return self.filter.matches(self.plugin)
    
    def __call__(self):
        """Run the global handler's main functionality.
        
        Override this method to check conditions and generate events.
        Call self.trigger_event(event_name, **kwargs) to fire events.
        """
        pass
    
    def trigger_event(self, event_name, **kwargs):
        """Trigger an event and call the associated effect.
        
        Args:
            event_name: String identifier for the event
            **kwargs: Additional event-specific parameters
        """
        if event_name in self.effects:
            try:
                self.effects[event_name](self, None, **kwargs)
            except Exception as e:
                logMessage(f"[ERROR] Effect '{event_name}' failed in {self.__class__.__name__}: {e}")
                import traceback
                logMessage(traceback.format_exc())

class GraphicHandler(ObjectHandler):
    """Class to handle image-related events and interactions."""
    KSIZE = 7  # Kernel size for Gaussian blur
    INVERT = 1  # 1 for normal, -1 for inverted depth map
    ELEVATION_SCALE = 0.5  # Scale factor for elevation commands
    
    def __init__(self, filter=GraphicFilter(), effects=None):
        super().__init__(filter, effects)
        
    def capture_callback(self, region, image):
        """Callback function when an image region is captured."""
         # Convert to grayscale
        map = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        # Apply guassian blur to reduce details
        map = cv2.GaussianBlur(map, (self.KSIZE, self.KSIZE), 0)
        
        # Normalize to 0-1
        map = map / 255.0
        
        # Invert if needed
        if self.INVERT == -1:
            map = 1.0 - map
            
        # Calculate relative position in the depth map
        mouse_pos = self.plugin.get_mouse_position()
        rel_x = int((mouse_pos[0] - region.left) * map.shape[1] / self.region.width)
        rel_y = int((mouse_pos[1] - region.top) * map.shape[0] / self.region.height)
        
        # Clamp coordinates
        rel_x = max(0, min(map.shape[1] - 1, rel_x))
        rel_y = max(0, min(map.shape[0] - 1, rel_y))
        
        # Get depth value
        depth_value = map[rel_y, rel_x]
        
        # Calculate elevation command
        elevation = (depth_value - 0.5) * 2.0 * self.ELEVATION_SCALE
        
        # Send elevation command to hardware
        self.plugin.hardware.send_elevation(elevation)
        
    def handle_event(self, event_name, obj, **kwargs):
        """Handle an event by calling the appropriate effect.
        
        Overrides base method to add capture callback on enter event.
        
        Args:
            event_name: String identifier for the event (e.g., 'enter', 'leave', 'gainFocus')
            obj: The NVDA object associated with the event
            **kwargs: Additional event-specific parameters
        """
        if event_name == 'enter':
            if not hasattr(obj, 'location') or not obj.location:
                return
        
            # Get the location
            self.plugin.add_capture_region(self, obj.location)
        elif event_name == 'leave':
            # Remove this handler's capture region from the plugin
            self.plugin.remove_capture_region(self)
        
        # Call base handler
        super().handle_event(event_name, obj, **kwargs)

class ScreenBorderHandler(GlobalHandler):
    """Class to handle mouse on screen border events."""
    
    def __init__(self, filter=GlobalFilter(), effects=None):
        super().__init__(filter, effects)
        self.on_border = False  # Track if mouse is currently on border
    
    def run(self):
        """Check mouse position and generate border events."""
        current_pos = self.plugin.get_mouse_position()
        full_screen_width, full_screen_height = self.plugin.get_screen_size()
        # Check if on screen border for continuous vibration feedback
        on_screen_border = (current_pos[0] <= 0 or current_pos[0] >= full_screen_width - 1 or
                            current_pos[1] <= 0 or current_pos[1] >= full_screen_height - 1)

        if on_screen_border != self.on_border:
            if on_screen_border:
                self.trigger_event('border_enter')
            else:
                self.trigger_event('border_leave')
            self.on_border = on_screen_border
       