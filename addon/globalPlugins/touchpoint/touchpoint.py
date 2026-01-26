# -*- coding: utf-8 -*-
# Touchpoint NVDA Global Plugin
# Captures UI element events for the Touchpoint project

import globalPluginHandler
import api
import ui
import eventHandler
import controlTypes
import NVDAObjects
import logHandler
import winUser
import threading
import time
import sys
import os
import ctypes
from .utils import logMessage, logUIElement
from .handlers import HandlerManager, ObjectHandler
from .handler_list import objectHandlerList, globalHandlerList
from .dependencies import np, cv2, songbird, DEPENDENCIES_AVAILABLE, IMPORT_ERROR
from .hardware_driver import HardwareDriver


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """
    Global plugin that monitors and logs NVDA UI element events.
    This plugin captures various events like focus changes, mouse movements,
    and object state changes. Integrates with screen capture and hardware driver
    for haptic feedback based on screen depth maps.
    """
    
    # Mouse check interval
    EVENT_CHECK_INTERVAL = 0.01
    
    def __init__(self):
        """Initialize the global plugin."""
        super(GlobalPlugin, self).__init__()
        
        # Overall plugin status
        self.enabled = True
        
        # Hardware configuration
        self.max_elevation_speed = 2.0 # units per second
        self.hardware = HardwareDriver()
        
        # Object handler manager
        self.objectHandlers = HandlerManager(self)
        self.objectHandlers.populate(objectHandlerList)
            
        # Global handler manager
        self.globalHandlers = HandlerManager(self)
        self.globalHandlers.populate(globalHandlerList)
        
        # Threading
        self.capture_thread = None
        self.event_thread = None
        
        # State variables
        self.curr_obj = None
        self.curr_obj_id = None
        
        # Capture callback system
        self.camera = None
        self.capture_regions = {}  # Dict mapping handler -> region
        self.depth_map_lock = threading.Lock()
        
        # Get full screen size for border detection
        self.screen_size = (ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1))  # (SM_CXSCREEN, SM_CYSCREEN)
        
        # Check dependencies after attributes are set
        if not DEPENDENCIES_AVAILABLE:
            logMessage(f"[ERROR] Touchpoint dependencies not available: {IMPORT_ERROR}")
            logMessage("You should have been prompted to install dependencies on first run.")
            ui.message("Touchpoint addon: Dependencies not installed. Check NVDA log for instructions.")
            self.enabled = False
            return
        
        # Start initialization in separate thread to avoid blocking NVDA startup
        init_thread = threading.Thread(target=self._initialize_async, daemon=True)
        init_thread.start()
        
        logMessage("Touchpoint NVDA addon initialized")
    
    def _initialize_async(self):
        """Initialize hardware driver and screen capture asynchronously."""
        try:
            # Initialize hardware driver
            self.hardware.initialize()
            
            # Set max elevation speed
            self.hardware.set_max_elevation_speed(self.max_elevation_speed)
            
            # Note: mss will be initialized in the capture thread due to thread-local storage requirements
            
            # Start screen capture thread (will only capture when enabled)
            self.capture_thread = threading.Thread(target=self._screen_capture_thread, daemon=True)
            self.capture_thread.start()
            
            # Start event tracking thread
            self.event_thread = threading.Thread(target=self._event_tracking_thread, daemon=True)
            self.event_thread.start()
            
            logMessage("Touchpoint NVDA addon running")
            
        except Exception as e:
            logMessage(f"[ERROR] Failed to initialize: {e}")
            import traceback
            logMessage(traceback.format_exc())
            self.enabled = False
    
    def add_capture_region(self, handler, region):
        """Register a screen region to be captured with a callback.
        
        Args:
            handler: The handler object requesting the capture (must have capture_callback method)
            region: LocationHelper object or tuple (left, top, right, bottom) defining the region
        """
        if not hasattr(handler, 'capture_callback'):
            logMessage(f"[ERROR] Handler {handler.__class__.__name__} has no capture_callback method")
            return
        
        with self.depth_map_lock:
            self.capture_regions[handler] = region
    
    def remove_capture_region(self, handler):
        """Remove a handler's capture region.
        
        Args:
            handler: The handler object to remove
        """
        with self.depth_map_lock:
            if handler in self.capture_regions:
                del self.capture_regions[handler]
    
    
    def _capture_screen_region(self, camera, region):
        """Capture a screen region as an image.
        
        Args:
            camera: mss instance
            region: LocationHelper object or tuple (left, top, right, bottom)
        
        Returns:
            numpy array of the captured image in BGR format, or None if capture fails
        """
        # Handle LocationHelper objects
        if hasattr(region, 'left'):
            left, top, right, bottom = region.left, region.top, region.right, region.bottom
        else:
            left, top, right, bottom = region
        
        # mss expects a dict with left, top, width, height
        monitor = {
            "left": left,
            "top": top,
            "width": right - left,
            "height": bottom - top
        }
        
        try:
            screenshot = camera.grab(monitor)
            if screenshot is None:
                return None
            
            # Convert mss screenshot to numpy array
            frame = np.array(screenshot)
            
            # mss returns BGRA, extract BGR channels
            frame = frame[:, :, :3]
            
            return frame
        except Exception as e:
            logMessage(f"[ERROR] Failed to capture region: {e}")
            return None
    
    def _screen_capture_thread(self):
        """Thread function to continuously capture screen regions and call callbacks."""
        # Create mss instance in this thread (mss uses thread-local storage)
        try:
            import mss
            camera = mss.mss()
        except Exception as e:
            logMessage(f"[ERROR] Failed to initialize mss in capture thread: {e}")
            return
        
        try:
            while self.enabled:
                try:
                    # Capture all registered regions
                    with self.depth_map_lock:
                        regions_to_capture = list(self.capture_regions.items())  # Create copy to avoid lock issues
                    
                    if regions_to_capture:
                        # Process each registered region
                        for handler, region in regions_to_capture:
                            # Capture the region
                            image = self._capture_screen_region(camera, region)
                            
                            if image is not None:
                                # Check if handler is still registered before calling callback
                                with self.depth_map_lock:
                                    if handler not in self.capture_regions:
                                        continue  # Handler was removed, skip callback
                                
                                try:
                                    # Call the handler's callback with region and image
                                    handler.capture_callback(region, image)
                                except Exception as e:
                                    logMessage(f"[ERROR] Callback failed for handler {handler.__class__.__name__}: {e}")
                        
                        # Small delay to prevent excessive CPU usage
                        time.sleep(0.01)
                    else:
                        # When not capturing, sleep longer to reduce CPU usage
                        time.sleep(0.05)
                    
                except Exception as e:
                    logMessage(f"[ERROR] Screen capture: {e}")
                    time.sleep(1)
        except Exception as e:
            logMessage(f"[ERROR] Screen capture thread failed: {e}")
    
    
    
    def _get_object_id(self, obj):
        """Get a unique identifier for an NVDA object.
        
        Returns a tuple that uniquely identifies the object using:
        - windowHandle
        - IAccessibleChildID (if available)
        - name
        - role
        """
        if not obj:
            return None
        
        try:
            # Start with window handle
            hwnd = obj.windowHandle if hasattr(obj, 'windowHandle') else None
            
            # Try to get IAccessible child ID
            child_id = None
            if hasattr(obj, 'IAccessibleChildID'):
                child_id = obj.IAccessibleChildID
                
            # Use name and role for more uniqueness
            name = obj.name if hasattr(obj, 'name') else None
            role = obj.role if hasattr(obj, 'role') else None
            
            return (hwnd, child_id, name, role)
        except:
            return None
        
    def get_mouse_position(self):
        """Get the current mouse position as (x, y)."""
        pos = winUser.getCursorPos()
        return pos
    
    def get_screen_size(self):
        """Get the full screen size as (width, height)."""
        return self.screen_size
    
    def _event_tracking_thread(self):
        """Thread to track mouse position and trigger handlers."""
        
        while self.enabled:
            # Get current mouse position using NVDA's winUser
            current_pos = winUser.getCursorPos()
                
            # Check what object is under the mouse cursor
            mouse_obj = NVDAObjects.NVDAObject.objectFromPoint(current_pos[0], current_pos[1])
                
            # Log IAccessible and IAccessible2 attributes for debugging
            if mouse_obj:
                # Gets unique id for object under mouse
                mouse_id = self._get_object_id(mouse_obj)
                
                # If there is a valid previous object, compare by ID
                if self.curr_obj:
                    if mouse_id != self.curr_obj_id:
                        # Call enter/leave handlers for object change
                        for handler in self.objectHandlers.handlers:
                            if handler.matches(self.curr_obj):
                                handler.handle_event('leave', self.curr_obj)
                        for handler in self.objectHandlers.handlers:
                            if handler.matches(mouse_obj):
                                handler.handle_event('enter', mouse_obj)
                
                # Update current object and ID
                self.curr_obj = mouse_obj
                self.curr_obj_id = mouse_id
            
            # Run global handlers
            for handler in self.globalHandlers.handlers:
                if handler.matches():
                    handler()
            time.sleep(self.EVENT_CHECK_INTERVAL)
    
    def terminate(self):
        """Clean up when the plugin is terminated."""
        logMessage("Touchpoint NVDA addon terminating...")
        self.enabled = False
        
        # Close hardware driver
        self.hardware.terminate()
        
        logMessage("Touchpoint NVDA addon terminated")
        super(GlobalPlugin, self).terminate()

    # Event handlers for various UI events
    
    def event_gainFocus(self, obj, nextHandler):
        """
        Triggered when an object gains focus.
        
        Args:
            obj: The object that gained focus
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "gainFocus")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('gainFocus', obj)
        nextHandler()

    def event_loseFocus(self, obj, nextHandler):
        """
        Triggered when an object loses focus.
        
        Args:
            obj: The object that lost focus
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "loseFocus")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('loseFocus', obj)
        nextHandler()

    def event_foreground(self, obj, nextHandler):
        """
        Triggered when a window comes to the foreground.
        
        Args:
            obj: The window object
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "foreground")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('foreground', obj)
        nextHandler()

    def event_nameChange(self, obj, nextHandler):
        """
        Triggered when an object's name changes.
        
        Args:
            obj: The object whose name changed
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "nameChange")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('nameChange', obj)
        nextHandler()

    def event_valueChange(self, obj, nextHandler):
        """
        Triggered when an object's value changes.
        
        Args:
            obj: The object whose value changed
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "valueChange")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('valueChange', obj)
        nextHandler()

    def event_stateChange(self, obj, nextHandler):
        """
        Triggered when an object's state changes.
        
        Args:
            obj: The object whose state changed
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "stateChange")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('stateChange', obj)
        nextHandler()

    def event_selection(self, obj, nextHandler):
        """
        Triggered when a selection is made.
        
        Args:
            obj: The selected object
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "selection")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('selection', obj)
        nextHandler()

    def event_mouseMove(self, obj, nextHandler, x=None, y=None):
        """
        Triggered when the mouse moves.
        
        Args:
            obj: The object under the mouse
            nextHandler: The next event handler in the chain
            x: Mouse X coordinate
            y: Mouse Y coordinate
        """
        nextHandler()

    def event_typedCharacter(self, obj, nextHandler, ch=None):
        """
        Triggered when a character is typed.
        
        Args:
            obj: The object where the character was typed
            nextHandler: The next event handler in the chain
            ch: The typed character
        """
        # logMessage(f"Typed character: {ch}")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('typedCharacter', obj, ch=ch)
        nextHandler()

    def event_caret(self, obj, nextHandler):
        """
        Triggered when the caret (text cursor) moves.
        
        Args:
            obj: The object containing the caret
            nextHandler: The next event handler in the chain
        """
        # This event is very frequent, uncomment to enable logging
        # logUIElement(obj, "caret")
        nextHandler()

    def event_menuStart(self, obj, nextHandler):
        """
        Triggered when a menu is opened.
        
        Args:
            obj: The menu object
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "menuStart")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('menuStart', obj)
        nextHandler()

    def event_menuEnd(self, obj, nextHandler):
        """
        Triggered when a menu is closed.
        
        Args:
            obj: The menu object
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "menuEnd")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('menuEnd', obj)
        nextHandler()

    def event_alert(self, obj, nextHandler):
        """
        Triggered when an alert or notification appears.
        
        Args:
            obj: The alert object
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "alert")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('alert', obj)
        nextHandler()

    def event_documentLoadComplete(self, obj, nextHandler):
        """
        Triggered when a document finishes loading.
        
        Args:
            obj: The document object
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "documentLoadComplete")
        # Calls matching object handlers
        for handler in self.objectHandlers.handlers:
            if handler.matches(obj):
                handler.handle_event('documentLoadComplete', obj)
        nextHandler()
