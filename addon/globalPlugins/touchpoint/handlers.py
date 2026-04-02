import threading
from xml.sax import handler
from .filters import GlobalFilter, ObjectFilter, GraphicFilter
from .utils import logMessage
from .effects import Effect
from .dependencies import cv2, np

class HandlerManager:
    """Class to manage multiple NVDA object handlers."""
    
    def __init__(self, plugin, handlers=None):
        self.plugin = plugin
        self.handlers = handlers if handlers is not None else []
        
    def add_handler(self, handler):
        """Add a new handler to the manager."""
        self.handlers.append(handler)
        handler.set_plugin(self.plugin)
    
    def populate(self, handler_list):
        """Populate handlers from a given list."""
        for handler in handler_list:
            self.add_handler(handler)

class ObjectHandlerManager(HandlerManager):
    def dispatch_event(self, event_name, obj, **kwargs):
        """Dispatch an event to all matching handlers.
        
        Args:
            event_name: The name of the event to dispatch
            obj: The NVDA object associated with the event
            **kwargs: Additional event-specific parameters
        """
        for handler in self.handlers:
            if handler.matches(obj):
                handler.handle_event(event_name, obj, **kwargs)

    def dispatch_mouse_transitions(self, obj, mouse_pos):
        """Dispatch enter/leave transitions per-handler using handler-owned entered object state."""
        for handler in self.handlers:
            if handler.entered_object is None:
                if handler.matches(obj):
                    handler.entered_object = obj
                    handler.handle_event('enter', obj)
                continue

            if handler.has_exited_entered_object(mouse_pos):
                previous_obj = handler.entered_object
                handler.entered_object = None
                handler.handle_event('leave', previous_obj)

                # If cursor is already inside another matching object after leaving, re-enter immediately.
                if handler.matches(obj):
                    handler.entered_object = obj
                    handler.handle_event('enter', obj)

class GlobalHandlerManager(HandlerManager):
    def dispatch_events(self):
        """Dispatch events for all active global handlers."""
        for handler in self.handlers:
            if handler.matches():
                handler()

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
        self.entered_object = None
        
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

    def has_exited_entered_object(self, mouse_pos):
        """Return True when cursor is outside current entered object's bounding box."""
        if self.entered_object is None:
            return True

        location = getattr(self.entered_object, 'location', None)
        if location is None:
            return True

        return not (
            location.left <= mouse_pos[0] < (location.left + location.width)
            and location.top <= mouse_pos[1] < (location.top + location.height)
        )
    
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

class ScreenBorderHandler(GlobalHandler):
    """Class to handle mouse on screen border events."""
    
    def __init__(self, filter=GlobalFilter(), effects=None):
        super().__init__(filter, effects)
        self.on_border = False  # Track if mouse is currently on border
    
    def __call__(self):
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
       