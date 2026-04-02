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
from .utils import Rect, logMessage, logUIElement
from .render_pipeline import RenderPipeline
from .dependencies import np, cv2, songbird, DEPENDENCIES_AVAILABLE, IMPORT_ERROR
from .hardware_driver import HardwareDriver
from .emulator_gui import TouchpointEmulatorGUI
from .config import TouchpointConfig


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """
    Global plugin that monitors and logs NVDA UI element events.
    This plugin captures various events like focus changes, mouse movements,
    and object state changes. Integrates with screen capture and hardware driver
    for haptic feedback based on screen depth maps.
    """
    
    def __init__(self):
        """Initialize the global plugin."""
        super(GlobalPlugin, self).__init__()
        
        # Overall plugin status
        self.enabled = True
        
        # Hardware configuration
        self.hardware = HardwareDriver(self)
        
        # Emulator GUI
        self.emulator_gui = TouchpointEmulatorGUI()
        
        # Get centralized configuration (singleton - only created once)
        self.config = TouchpointConfig.get_instance()
        capture_width, capture_height = self.config.capture_dimensions
        
        # Initialize render pipeline (includes handler managers)
        self.render_pipeline = RenderPipeline(self)
        
        # Capture region configuration - centered on mouse with size from software config
        self.capture_region_width = capture_width
        self.capture_region_height = capture_height
        self.capture_region_lock = threading.Lock()
        
        # Initialize all layers with starting region bounds
        initial_region = Rect(left=0, top=0, width=self.capture_region_width, height=self.capture_region_height)
        self.render_pipeline.update_layer_regions(initial_region)
        
        # Render thread
        self.render_thread = None
        
        # Mouse position tracking (updated by event thread)
        self.mouse_position = (0, 0)
        self.mouse_position_lock = threading.Lock()
        self.last_mouse_object = None
        self.last_mouse_object_id = None
        
        # Debug logging timer
        self.last_debug_log_time = 0
        
        # Get full screen size
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
            
            # Initialize renderers
            self.render_pipeline.initialize_renderers()
            
            # Initialize emulator with layer IDs and aspect ratio
            layer_ids = self.render_pipeline.get_layer_ids()
            aspect_ratio = self.capture_region_width / self.capture_region_height
            self.emulator_gui.initialize_layers(layer_ids, aspect_ratio)
            
            # Initialize emulator with hardware settings
            self.emulator_gui.set_max_elevation(self.hardware.max_elevation)
            self.emulator_gui.set_elevation_speed(self.hardware.max_elevation_speed)
            self.emulator_gui.set_capture_region_size(
                self.capture_region_width,
                self.capture_region_height,
                pixels_per_mm=self.hardware.last_pixels_per_mm,
            )
            
            # Start render thread
            self.render_thread = threading.Thread(target=self._render_thread, daemon=True)
            self.render_thread.start()
            
            logMessage("Touchpoint NVDA addon running")
            
        except Exception as e:
            logMessage(f"[ERROR] Failed to initialize: {e}")
            import traceback
            logMessage(traceback.format_exc())
            self.enabled = False
        
    def get_mouse_position(self):
        """Get the current mouse position as (x, y).
        
        Returns the cached position updated by the render thread.
        """
        with self.mouse_position_lock:
            return self.mouse_position

    def get_mouse_object(self):
        """Get the object currently tracked under the mouse by the render thread."""
        return self.last_mouse_object

    def _get_object_identity(self, obj):
        """Build a stable identity key for mouse-enter/leave object tracking."""
        if obj is None:
            return None

        location = getattr(obj, 'location', None)
        location_key = None
        if location is not None:
            location_key = (location.left, location.top, location.width, location.height)

        return (
            getattr(obj, 'windowHandle', None),
            getattr(obj, 'role', None),
            getattr(obj, 'name', None),
            location_key,
        )

    def _dispatch_mouse_object_transitions(self, mouse_pos):
        """Dispatch object enter/leave events from render thread using per-handler state."""
        try:
            obj = NVDAObjects.NVDAObject.objectFromPoint(int(mouse_pos[0]), int(mouse_pos[1]))
            self.last_mouse_object = obj
            self.last_mouse_object_id = self._get_object_identity(obj)
            self.render_pipeline.object_handler_manager.dispatch_mouse_transitions(obj, mouse_pos)
        except Exception as e:
            logMessage(f"[ERROR] Mouse object transition dispatch failed: {e}")
    
    def _log_object_under_mouse(self, mouse_pos):
        """Log information about the object under the mouse cursor.
        
        Args:
            mouse_pos: Tuple (x, y) of mouse position in screen coordinates
        """
        try:
            # Get object layer
            object_layer = self.render_pipeline.object_layer
            
            # Convert mouse position to object layer local coordinates
            region = object_layer.current_region
            local_x = mouse_pos[0] - region.left
            local_y = mouse_pos[1] - region.top
            
            # Check if local position is within the layer bounds
            if local_x < 0 or local_y < 0 or local_x >= region.width or local_y >= region.height:
                return
            
            # Get the label at this position
            object_img = object_layer.get_image()
            if object_img.size == 0:
                return
            
            if local_y >= object_img.shape[0] or local_x >= object_img.shape[1]:
                return
            
            label = object_img[local_y, local_x]
            
            if label == 0:
                # No object
                return
            
            # Get label data
            label_data = object_layer.label_map.get(label)
            if label_data is None:
                return
            
            # Extract object info
            obj_info = label_data.get('obj_info', {})
            obj = obj_info.get('object')
            obj_name = obj_info.get('name', 'Unknown')
            obj_role = obj_info.get('role', 'Unknown')
            obj_value = getattr(obj, 'value', 'N/A') if obj else 'N/A'
            location = label_data.get('location')
            
            # Format log message
            log_msg = f"Label: {label} | Name: {obj_name} | Role: {obj_role} | Value: {obj_value} | Location: {location}"
            self.hardware.send_debug_log(log_msg)
            
        except Exception as e:
            # Silently ignore errors to avoid flooding logs
            pass
    
    def get_screen_size(self):
        """Get the full screen size as (width, height)."""
        return self.screen_size

    def set_capture_region_size(self, width, height, pixels_per_mm=None):
        """Dynamically update capture region size in pixels."""
        width = max(1, int(round(width)))
        height = max(1, int(round(height)))

        resized = False
        with self.capture_region_lock:
            if width != self.capture_region_width or height != self.capture_region_height:
                self.capture_region_width = width
                self.capture_region_height = height
                resized = True

        if resized:
            logMessage(f"Capture region resized to {width}x{height} px (ppm={pixels_per_mm})")

        if self.emulator_gui:
            self.emulator_gui.set_capture_region_size(width, height, pixels_per_mm=pixels_per_mm)
    
    def _render_thread(self):
        """Thread to track mouse position and trigger handlers."""
        
        while self.enabled:
            # Update current mouse position using NVDA's winUser
            current_pos = winUser.getCursorPos()
            
            with self.mouse_position_lock:
                # Update mouse position variable
                self.mouse_position = current_pos

            # Route mouse enter/leave event transitions through render thread to avoid cross-thread conflicts.
            self._dispatch_mouse_object_transitions(current_pos)
            
            with self.capture_region_lock:
                capture_w = self.capture_region_width
                capture_h = self.capture_region_height

            # Calculate new region centered on current mouse position
            new_region = Rect(
                left=current_pos[0] - capture_w // 2,
                top=current_pos[1] - capture_h // 2,
                width=capture_w,
                height=capture_h
            )
            
            # Update region bounds and execute render cycle
            self.render_pipeline.update_layer_regions(new_region)
            self.render_pipeline.execute_render_cycle()
                
            # Update emulator with layer images if emulator is open
            if self.emulator_gui.is_window_open():
                for layer in self.render_pipeline.get_layers():
                    self.emulator_gui.update_layer_image(layer.id, layer.image)
            
            # Cycle layer states for next frame (must happen every frame, not just when emulator is open)
            self.render_pipeline.cycle_layer_states()
            
            # Run global handlers
            self.render_pipeline.dispatch_handlers()
                    
            # Cycle hardware state machine
            self.hardware.cycle_state()
            time.sleep(self.config.software['threading']['render'])
    
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
        self.render_pipeline.object_handler_manager.dispatch_event('gainFocus', obj)
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
        self.render_pipeline.object_handler_manager.dispatch_event('loseFocus', obj)
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
        self.render_pipeline.object_handler_manager.dispatch_event('foreground', obj)
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
        self.render_pipeline.object_handler_manager.dispatch_event('nameChange', obj)
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
        self.render_pipeline.object_handler_manager.dispatch_event('valueChange', obj)
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
        self.render_pipeline.object_handler_manager.dispatch_event('stateChange', obj)
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
        self.render_pipeline.object_handler_manager.dispatch_event('selection', obj)
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
        self.render_pipeline.object_handler_manager.dispatch_event('typedCharacter', obj, ch=ch)
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
        self.render_pipeline.object_handler_manager.dispatch_event('menuStart', obj)
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
        self.render_pipeline.object_handler_manager.dispatch_event('menuEnd', obj)
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
        self.render_pipeline.object_handler_manager.dispatch_event('alert', obj)
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
        self.render_pipeline.object_handler_manager.dispatch_event('documentLoadComplete', obj)
        nextHandler()
        
    def event_scrollPositionChanged(self, obj, nextHandler):
        """
        Triggered when an object's scroll position changes.
        
        Args:
            obj: The object whose scroll position changed
            nextHandler: The next event handler in the chain
        """
        # logUIElement(obj, "scrollPositionChanged")
        # Calls matching object handlers
        self.render_pipeline.object_handler_manager.dispatch_event('scrollPositionChanged', obj)
        nextHandler()
    
    # Script to open emulator GUI
    def script_openEmulator(self, gesture):
        """Open the hardware emulator GUI window."""
        ui.message("Opening Touchpoint emulator")
        
        # Ensure emulator is initialized with latest layer info
        layer_ids = self.render_pipeline.get_layer_ids()
        aspect_ratio = self.capture_region_width / self.capture_region_height
        self.emulator_gui.initialize_layers(layer_ids, aspect_ratio)
        
        # Update emulator with current hardware settings
        self.emulator_gui.set_max_elevation(self.hardware.max_elevation)
        self.emulator_gui.set_elevation_speed(self.hardware.max_elevation_speed)
        
        # Open the window
        self.emulator_gui.open_window(self.hardware.hardware_connected)
    
    # NVDA will automatically bind NVDA+shift+e to this script
    script_openEmulator.__doc__ = "Open Touchpoint hardware emulator"
    
    __gestures = {
        "kb:NVDA+shift+e": "openEmulator",
    }
