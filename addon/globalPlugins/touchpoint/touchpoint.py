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
from .handlers import ObjectHandlerManager, GlobalHandlerManager
from .render_config import initialize_render_config
from .dependencies import np, cv2, songbird, DEPENDENCIES_AVAILABLE, IMPORT_ERROR
from .hardware_driver import HardwareDriver
from .emulator_gui import TouchpointEmulatorGUI
from .render_layers import Region


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """
    Global plugin that monitors and logs NVDA UI element events.
    This plugin captures various events like focus changes, mouse movements,
    and object state changes. Integrates with screen capture and hardware driver
    for haptic feedback based on screen depth maps.
    """
    
    # Event checking and rendering interval
    RENDER_INTERVAL = 0.01
    
    def __init__(self):
        """Initialize the global plugin."""
        super(GlobalPlugin, self).__init__()
        
        # Overall plugin status
        self.enabled = True
        
        # Hardware configuration
        self.hardware = HardwareDriver(self)
        
        # Emulator GUI
        self.emulator_gui = TouchpointEmulatorGUI()
        
        # Initialize render configuration (layers, renderers, handlers)
        renderLayerList, rendererList, objectHandlerList, globalHandlerList = initialize_render_config(self)
        
        # Object handler manager
        self.objectHandlers = ObjectHandlerManager(self)
        self.objectHandlers.populate(objectHandlerList)
            
        # Global handler manager
        self.globalHandlers = GlobalHandlerManager(self)
        self.globalHandlers.populate(globalHandlerList)
        
        # Capture region configuration - centered on mouse with fixed size
        self.capture_region_width = 100
        self.capture_region_height = 100
        
        # Render layers and renderers
        self.renderLayers = renderLayerList
        for layer in self.renderLayers:
            layer.set_plugin(self)
            # Initialize with first region bounds
            initial_region = Region(left=0, top=0, width=self.capture_region_width, height=self.capture_region_height)
            layer.update_region_bounds(initial_region)
            
        self.renderers = rendererList
        for renderer in self.renderers:
            renderer.set_plugin(self)
        
        # Render thread
        self.render_thread = None
        
        # Mouse position tracking (updated by event thread)
        self.mouse_position = (0, 0)
        self.mouse_position_lock = threading.Lock()
        
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
            
            # Initialize renderers if needed (currently they are just data containers, but this is where setup would go)
            for renderer in self.renderers:
                renderer.initialize()
            
            # Initialize emulator with layer IDs and aspect ratio
            layer_ids = [layer.id for layer in self.renderLayers]
            aspect_ratio = self.capture_region_width / self.capture_region_height
            self.emulator_gui.initialize_layers(layer_ids, aspect_ratio)
            
            # Initialize emulator with hardware settings
            self.emulator_gui.set_max_elevation(self.hardware.max_elevation)
            self.emulator_gui.set_elevation_speed(self.hardware.max_elevation_speed)
            
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
    
    def get_screen_size(self):
        """Get the full screen size as (width, height)."""
        return self.screen_size
    
    def _render_thread(self):
        """Thread to track mouse position and trigger handlers."""
        
        while self.enabled:
            # Update current mouse position using NVDA's winUser
            current_pos = winUser.getCursorPos()
            
            with self.mouse_position_lock:
                # Update mouse position variable
                self.mouse_position = current_pos
            
            # Update region bounds for all layers before running renderers
            for layer in self.renderLayers:
                # Calculate new region centered on current mouse position
                new_region = Region(
                    left=current_pos[0] - self.capture_region_width // 2,
                    top=current_pos[1] - self.capture_region_height // 2,
                    width=self.capture_region_width,
                    height=self.capture_region_height
                )
                layer.update_region_bounds(new_region)
                
            # Execute renderers in order
            for renderer in self.renderers:
                renderer()
                
            # Update emulator with layer images if emulator is open
            if self.emulator_gui.is_window_open():
                for layer in self.renderLayers:
                    self.emulator_gui.update_layer_image(layer.id, layer.image)
                    layer.cycle_state()  # Cycle layer state for next frame
            
            # Run global handlers
            self.globalHandlers.dispatch_events()
                    
            # Cycle hardware state machine
            self.hardware.cycle_state()
            time.sleep(self.RENDER_INTERVAL)
    
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
        self.objectHandlers.dispatch_event('gainFocus', obj)
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
        self.objectHandlers.dispatch_event('loseFocus', obj)
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
        self.objectHandlers.dispatch_event('foreground', obj)
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
        self.objectHandlers.dispatch_event('nameChange', obj)
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
        self.objectHandlers.dispatch_event('valueChange', obj)
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
        self.objectHandlers.dispatch_event('stateChange', obj)
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
        self.objectHandlers.dispatch_event('selection', obj)
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
        self.objectHandlers.dispatch_event('typedCharacter', obj, ch=ch)
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
        self.objectHandlers.dispatch_event('menuStart', obj)
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
        self.objectHandlers.dispatch_event('menuEnd', obj)
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
        self.objectHandlers.dispatch_event('alert', obj)
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
        self.objectHandlers.dispatch_event('documentLoadComplete', obj)
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
        self.objectHandlers.dispatch_event('scrollPositionChanged', obj)
        nextHandler()
    
    # Script to open emulator GUI
    def script_openEmulator(self, gesture):
        """Open the hardware emulator GUI window."""
        ui.message("Opening Touchpoint emulator")
        
        # Ensure emulator is initialized with latest layer info
        layer_ids = [layer.id for layer in self.renderLayers]
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
