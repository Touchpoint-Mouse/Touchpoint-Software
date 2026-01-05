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

# Check and setup dependencies first
module_path = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, module_path)
import dependency_checker

# Add parent directory to path to import songbird
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# Expand dependencies path if available
dependency_checker.expand_path()

# Import only numpy and songbird at module level
# dxcam will be imported lazily when needed to avoid DirectX access violations during NVDA startup
try:
    import numpy as np
    from songbird import SongbirdUART
    DEPENDENCIES_AVAILABLE = True
    IMPORT_ERROR = None
except ImportError as e:
    DEPENDENCIES_AVAILABLE = False
    IMPORT_ERROR = str(e)
    np = None
    SongbirdUART = None


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """
    Global plugin that monitors and logs NVDA UI element events.
    This plugin captures various events like focus changes, mouse movements,
    and object state changes. Integrates with screen capture and Songbird UART
    for haptic feedback based on screen depth maps.
    """
    
    # Configuration
    SERIAL_PORT = "COM6"
    SERIAL_BAUD_RATE = 460800
    
    # Header definitions
    H_PING = 0xFF
    H_ELEVATION = 0x10
    H_VIBRATION = 0x20
    
    # Depth map settings
    ELEVATION_SCALE = 0.5
    INVERT = 1  # 1 or -1
    
    # Mouse check interval
    MOUSE_CHECK_INTERVAL = 0.01
    
    def __init__(self):
        """Initialize the global plugin."""
        super(GlobalPlugin, self).__init__()
        
        # Initialize all attributes first to avoid AttributeError
        self.enabled = True
        self.depth_map = None
        self.depth_map_lock = threading.Lock()
        self.screen_size = None
        self.uart = None
        self.core = None
        self.capture_thread = None
        self.mouse_thread = None
        self.last_mouse_pos = None
        self.on_border = False
        self.capture_enabled = False
        self.camera = None
        self.current_image_obj = None
        self.image_region = None
        
        # Check dependencies after attributes are set
        if not DEPENDENCIES_AVAILABLE:
            self.logMessage(f"[ERROR] Touchpoint dependencies not available: {IMPORT_ERROR}")
            self.logMessage("You should have been prompted to install dependencies on first run.")
            ui.message("Touchpoint addon: Dependencies not installed. Check NVDA log for instructions.")
            self.enabled = False
            return
        
        # Start initialization in separate thread to avoid blocking NVDA startup
        init_thread = threading.Thread(target=self._initialize_async, daemon=True)
        init_thread.start()
        
        self.logMessage("Touchpoint NVDA addon initialized")
    
    def _initialize_async(self):
        """Initialize Songbird UART and screen capture asynchronously."""
        try:
            # Initialize UART
            self.uart = SongbirdUART("Touchpoint NVDA Addon")
            self.core = self.uart.get_protocol()
            
            if not self.uart.begin(self.SERIAL_PORT, self.SERIAL_BAUD_RATE):
                self.logMessage(f"[ERROR] Failed to open serial port {self.SERIAL_PORT}")
                self.enabled = False
                return
            
            # Wait for device ping
            self._wait_for_ping()
            
            # Lazy import dxcam here to avoid DirectX initialization during NVDA startup
            try:
                import dxcam
                self.camera = dxcam.create()
                self.logMessage("DXcam initialized (capture inactive until mouse over image)")
            except Exception as e:
                self.logMessage(f"[ERROR] Failed to initialize dxcam: {e}")
                self.enabled = False
                return
            
            # Start screen capture thread (will only capture when enabled)
            self.capture_thread = threading.Thread(target=self._screen_capture_thread, daemon=True)
            self.capture_thread.start()
            
            # Start mouse tracking thread
            self.mouse_thread = threading.Thread(target=self._mouse_tracking_thread, daemon=True)
            self.mouse_thread.start()
            
            self.logMessage("Touchpoint NVDA addon fully initialized and running")
            
        except Exception as e:
            self.logMessage(f"[ERROR] Failed to initialize: {e}")
            import traceback
            self.logMessage(traceback.format_exc())
            self.enabled = False
    
    def _wait_for_ping(self):
        """Wait for ping response from microcontroller."""
        time.sleep(1)
        self.core.flush()
        self.logMessage("Waiting for ping from microcontroller...")
        
        response = None
        timeout_count = 0
        while not response and timeout_count < 10:
            response = self.core.wait_for_header(self.H_PING, 1000)
            self.core.flush()
            timeout_count += 1
        
        if response:
            # Send response back to acknowledge ping
            pkt = self.core.create_packet(self.H_PING)
            self.core.send_packet(pkt)
            self.logMessage("Ping received from microcontroller")
        else:
            raise Exception("Timeout waiting for microcontroller ping")
    
    def _capture_screen_as_depth_map(self, camera, region=None):
        """Capture screen (or region) and convert it to a depth map.
        
        Args:
            camera: DXcam camera instance
            region: Tuple (left, top, right, bottom) to crop, or None for full screen
        """
        if region:
            # Capture only the specified region
            frame = camera.grab(region=region)
        else:
            # Capture full screen
            frame = camera.grab()
        
        if frame is None:
            return None, None
        
        # Convert to grayscale
        gray = np.mean(frame, axis=2).astype(np.float32)
        
        # Normalize to 0-1
        depth_map = gray / 255.0
        
        # Invert if needed
        if self.INVERT == -1:
            depth_map = 1.0 - depth_map
        
        return depth_map, (frame.shape[1], frame.shape[0])
    
    def _screen_capture_thread(self):
        """Thread function to continuously capture and update the screen depth map."""
        try:
            while self.enabled:
                try:
                    # Only capture when enabled (mouse over image)
                    if self.capture_enabled and self.camera and self.image_region:
                        new_depth_map, region_size = self._capture_screen_as_depth_map(self.camera, self.image_region)
                        
                        if new_depth_map is None:
                            time.sleep(0.001)
                            continue
                        
                        with self.depth_map_lock:
                            self.depth_map = new_depth_map
                            # Store the region size, not full screen size
                            self.screen_size = region_size
                    else:
                        # When not capturing, sleep longer to reduce CPU usage
                        time.sleep(0.05)
                    
                except Exception as e:
                    self.logMessage(f"[ERROR] Screen capture: {e}")
                    time.sleep(1)
        except Exception as e:
            self.logMessage(f"[ERROR] Screen capture thread failed: {e}")
    
    def _is_image_object(self, obj):
        """Check if an NVDA object is an image."""
        if not obj:
            return False
        
        try:
            role = obj.role if hasattr(obj, 'role') else None
            if role is None:
                return False
            
            # Check if role is GRAPHIC or IMAGE
            try:
                # Try new controlTypes.Role enum (NVDA 2021+)
                return role in (controlTypes.Role.GRAPHIC, controlTypes.Role.IMAGE)
            except:
                # Fallback for older NVDA versions
                try:
                    return role in (controlTypes.ROLE_GRAPHIC, controlTypes.ROLE_IMAGE)
                except:
                    return False
        except:
            return False
    
    def _get_elevation_at_position(self, x, y):
        """Get elevation value at a specific screen position.
        
        Args:
            x, y: Screen coordinates
        """
        with self.depth_map_lock:
            if self.depth_map is None or self.image_region is None:
                return None
            
            # Convert screen coordinates to image-relative coordinates
            left, top, right, bottom = self.image_region
            rel_x = x - left
            rel_y = y - top
            
            # Check if within image bounds
            if rel_x < 0 or rel_y < 0 or rel_x >= (right - left) or rel_y >= (bottom - top):
                return None
            
            # Map to depth map coordinates
            map_x = int(rel_x)
            map_y = int(rel_y)
            
            # Clamp to valid range
            map_x = max(0, min(map_x, self.depth_map.shape[1] - 1))
            map_y = max(0, min(map_y, self.depth_map.shape[0] - 1))
            
            # Get elevation from depth map
            elevation = self.depth_map[map_y, map_x] * self.ELEVATION_SCALE
            
            return elevation
    
    def _mouse_tracking_thread(self):
        """Thread to track mouse position and send elevation commands."""
        # Get full screen size for border detection
        full_screen_width = winUser.getSystemMetrics(winUser.SM_CXSCREEN)
        full_screen_height = winUser.getSystemMetrics(winUser.SM_CYSCREEN)
        
        while self.enabled:
            try:
                # Get current mouse position using NVDA's winUser
                current_pos = winUser.getCursorPos()
                
                # Send elevation commands when over image
                if self.capture_enabled:
                    elevation = self._get_elevation_at_position(current_pos[0], current_pos[1])
                    
                    if elevation is not None and self.core:
                        pkt = self.core.create_packet(self.H_ELEVATION)
                        pkt.write_float(elevation)
                        self.core.send_packet(pkt)
                
                # Check if on screen border for continuous vibration feedback
                on_screen_border = (current_pos[0] <= 0 or current_pos[0] >= full_screen_width - 1 or
                                   current_pos[1] <= 0 or current_pos[1] >= full_screen_height - 1)
                
                if on_screen_border != self.on_border:
                    if on_screen_border:
                        # Send continuous vibration command
                        pkt = self.core.create_packet(self.H_VIBRATION)
                        pkt.write_float(0.05)  # amplitude
                        pkt.write_float(100)   # frequency
                        pkt.write_int16(0)     # duration (continuous)
                        self.core.send_packet(pkt)
                    else:
                        # Send vibration off command
                        pkt = self.core.create_packet(self.H_VIBRATION)
                        pkt.write_float(0)
                        pkt.write_float(0)
                        pkt.write_int16(0)
                        self.core.send_packet(pkt)
                    self.on_border = on_screen_border
                
                time.sleep(self.MOUSE_CHECK_INTERVAL)
                
            except Exception as e:
                self.logMessage(f"[ERROR] Mouse tracking: {e}")
                time.sleep(1)
    
    def _handle_image_enter(self, obj):
        """Handle mouse entering an image object."""
        if not obj or not hasattr(obj, 'location') or not obj.location:
            return
        
        self.current_image_obj = obj
        loc = obj.location
        self.image_region = (loc.left, loc.top, loc.right, loc.bottom)
        self.capture_enabled = True
        
        self.logMessage(f"Mouse entered image: {obj.name if obj.name else 'Unnamed'} at {self.image_region}")
        
        # Send brief vibration pulse on entering image
        if self.core:
            pkt = self.core.create_packet(self.H_VIBRATION)
            pkt.write_float(0.08)  # amplitude
            pkt.write_float(150)   # frequency
            pkt.write_int16(100)   # duration (100ms pulse)
            self.core.send_packet(pkt)
    
    def _handle_image_leave(self):
        """Handle mouse leaving an image object."""
        self.capture_enabled = False
        self.current_image_obj = None
        self.image_region = None
        
        with self.depth_map_lock:
            self.depth_map = None
        
        self.logMessage("Mouse left image - capture disabled")
        
        # Send brief vibration pulse on leaving image
        if self.core:
            pkt = self.core.create_packet(self.H_VIBRATION)
            pkt.write_float(0.06)  # amplitude (slightly lower)
            pkt.write_float(100)   # frequency
            pkt.write_int16(80)    # duration (80ms pulse)
            self.core.send_packet(pkt)
            # Send zero elevation when leaving image
            pkt = self.core.create_packet(self.H_ELEVATION)
            pkt.write_float(0.0)
            self.core.send_packet(pkt)
    
    def terminate(self):
        """Clean up when the plugin is terminated."""
        self.logMessage("Touchpoint NVDA addon terminating...")
        self.enabled = False
        
        # Close UART connection
        if self.uart:
            try:
                self.uart.close()
            except:
                pass
        
        self.logMessage("Touchpoint NVDA addon terminated")
        super(GlobalPlugin, self).terminate()

    def logMessage(self, message):
        """Log a message to the NVDA log.
        """
        logHandler.log.info(message)

    def logUIElement(self, obj, eventName):
        """
        Log information about a UI element.
        
        Args:
            obj: The NVDA object
            eventName (str): The name of the event
        """
        try:
            name = obj.name if obj.name else "Unnamed"
            role = obj.role if hasattr(obj, 'role') else None
            
            # Get the human-readable role name from controlTypes
            if role is not None:
                try:
                    roleName = controlTypes.Role(role).displayString
                except:
                    # Fallback for older NVDA versions
                    try:
                        roleName = controlTypes.roleLabels.get(role, f"Unknown({role})")
                    except:
                        roleName = str(role)
            else:
                roleName = "Unknown"
            
            value = obj.value if hasattr(obj, 'value') and obj.value else ""
            states = obj.states if hasattr(obj, 'states') else set()
            location = obj.location if hasattr(obj, 'location') else None
            
            info = {
                'event': eventName,
                'name': name,
                'role': roleName,
                'value': value,
                'states': str(states),
                'location': location
            }
            
            self.logMessage(f"Event: {eventName} | Name: {name} | Role: {roleName}")
            
            # You can extend this to send data to external systems
            # For example: send to serial port, TCP socket, or save to file
            
        except Exception as e:
            self.logMessage(f"Error logging UI element: {str(e)}")

    # Event handlers for various UI events
    
    def event_gainFocus(self, obj, nextHandler):
        """
        Triggered when an object gains focus.
        
        Args:
            obj: The object that gained focus
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "gainFocus")
        nextHandler()

    def event_loseFocus(self, obj, nextHandler):
        """
        Triggered when an object loses focus.
        
        Args:
            obj: The object that lost focus
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "loseFocus")
        nextHandler()

    def event_foreground(self, obj, nextHandler):
        """
        Triggered when a window comes to the foreground.
        
        Args:
            obj: The window object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "foreground")
        nextHandler()

    def event_nameChange(self, obj, nextHandler):
        """
        Triggered when an object's name changes.
        
        Args:
            obj: The object whose name changed
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "nameChange")
        nextHandler()

    def event_valueChange(self, obj, nextHandler):
        """
        Triggered when an object's value changes.
        
        Args:
            obj: The object whose value changed
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "valueChange")
        nextHandler()

    def event_stateChange(self, obj, nextHandler):
        """
        Triggered when an object's state changes.
        
        Args:
            obj: The object whose state changed
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "stateChange")
        nextHandler()

    def event_selection(self, obj, nextHandler):
        """
        Triggered when a selection is made.
        
        Args:
            obj: The selected object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "selection")
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
        # Check if we entered or left an image
        is_image = self._is_image_object(obj)
        was_on_image = self.current_image_obj is not None
        
        if is_image and not was_on_image:
            # Entered an image
            self._handle_image_enter(obj)
        elif not is_image and was_on_image:
            # Left an image
            self._handle_image_leave()
        elif is_image and was_on_image and obj != self.current_image_obj:
            # Switched to a different image
            self._handle_image_leave()
            self._handle_image_enter(obj)
        
        nextHandler()

    def event_typedCharacter(self, obj, nextHandler, ch=None):
        """
        Triggered when a character is typed.
        
        Args:
            obj: The object where the character was typed
            nextHandler: The next event handler in the chain
            ch: The typed character
        """
        self.logMessage(f"Typed character: {ch}")
        nextHandler()

    def event_caret(self, obj, nextHandler):
        """
        Triggered when the caret (text cursor) moves.
        
        Args:
            obj: The object containing the caret
            nextHandler: The next event handler in the chain
        """
        # This event is very frequent, uncomment to enable logging
        # self.logUIElement(obj, "caret")
        nextHandler()

    def event_menuStart(self, obj, nextHandler):
        """
        Triggered when a menu is opened.
        
        Args:
            obj: The menu object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "menuStart")
        nextHandler()

    def event_menuEnd(self, obj, nextHandler):
        """
        Triggered when a menu is closed.
        
        Args:
            obj: The menu object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "menuEnd")
        nextHandler()

    def event_alert(self, obj, nextHandler):
        """
        Triggered when an alert or notification appears.
        
        Args:
            obj: The alert object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "alert")
        nextHandler()

    def event_documentLoadComplete(self, obj, nextHandler):
        """
        Triggered when a document finishes loading.
        
        Args:
            obj: The document object
            nextHandler: The next event handler in the chain
        """
        self.logUIElement(obj, "documentLoadComplete")
        nextHandler()
