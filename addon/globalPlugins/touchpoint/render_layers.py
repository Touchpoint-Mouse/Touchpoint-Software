from collections import OrderedDict
import threading
from .dependencies import np
from .utils import logMessage
import time
import NVDAObjects

class LayerManager:
    """Class to manage multiple render layers"""
    
    def __init__(self, plugin, layers=None):
        self.plugin = plugin
        self.layers = OrderedDict()
        if layers is not None:
            for layer in layers:
                self.add_layer(layer)
        
    def add_layer(self, layer):
        """Add a new layer to the manager."""
        self.layers[layer.id] = layer
        layer.set_plugin(self.plugin)
    
    def populate(self, layer_list):
        """Populate layers from a given list."""
        for layer in layer_list:
            self.add_layer(layer)
            
    def render_all_layers(self):
        """Update and render all layers."""
        for layer in self.layers.values():
            layer.update_mouse_change(self.plugin.get_mouse_position())
        
        for layer in self.layers.values():
            layer()
            
class RenderLayer:
    """Base class for render layers."""
    
    def __init__(self, renderers=None, effectors=None):
        self.plugin = None
        self.id = self.__class__.__name__
        self.renderers = renderers or []
        self.effectors = effectors or []
        
        # Render image
        self.image = np.array([])  # Placeholder for the rendered image data
        # Lock for synchronizing access to the image
        self.image_lock = threading.Lock()
        
    def __call__(self):
        """Render the layer and then apply effects"""
        for renderer in self.renderers:
            self.update_image(renderer(self.image))
            
        for effector in self.effectors:
            effector(self.image)
    
    def update_image(self, new_image):
        """Update the rendered image with thread safety.
        """
        with self.image_lock:
            self.image = new_image
        
    def set_plugin(self, plugin):
        """Set the parent plugin for this layer."""
        self.plugin = plugin
        
    def initialize(self):
        """Initialize the render layer."""
        pass
    
    def update_mouse_change(self, mouse_diff):
        """Update the layer based on change in mouse position."""
        pass
    
    def update_region_size(self, region):
        """Update the layer based on change in region size."""
        # Saves current image
        with self.image_lock:
            oldImage = self.image.copy()
            # Creates blank image with new region size
            self.update_image(np.zeros((region.height, region.width, 3), dtype=np.uint8))
            
    def get_absolute_region(self):
        """Get the absolute screen region for this layer."""
        if self.plugin is None:
            return (0, 0, 0, 0)
        
        # Get relative region from plugin
        relative_region = self.plugin.get_capture_region(self)
        if relative_region is None:
            return (0, 0, 0, 0)
        
        # Convert to absolute screen coordinates
        abs_left = relative_region.left
        abs_top = relative_region.top
        abs_right = relative_region.left + relative_region.width
        abs_bottom = relative_region.top + relative_region.height
        
        return (abs_left, abs_top, abs_right, abs_bottom)

class CaptureLayer(RenderLayer):
    def __init__(self):
        super().__init__()
        self.enabled = False
        self.capture_thread = None
        
    def initialize(self):
        """Initialize the capture layer."""
        self.enabled = True
        self.capture_thread = threading.Thread(target=self._screen_capture_thread, daemon=True)
        self.capture_thread.start()
        
    def _capture_screen_region(self, camera, region):
        """Capture a screen region as an image.
        
        Args:
            camera: mss instance
            region: LocationHelper object or tuple (left, top, right, bottom)
        
        Returns:
            numpy array of the captured image in BGR format, or None if capture fails
        """
        # Get region bounding box
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
                    # Capture the region
                    self.update_image(self._capture_screen_region(camera, self.get_absolute_region()))
                            
                    # Small delay to prevent excessive CPU usage
                    time.sleep(0.01)
                    
                except Exception as e:
                    logMessage(f"[ERROR] Screen capture: {e}")
                    time.sleep(1)
        except Exception as e:
            logMessage(f"[ERROR] Screen capture thread failed: {e}")

class SemanticLayer(RenderLayer):
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
        
    def _semantic_tracking_thread(self):
            # Check what object is under the mouse cursor
            try :
                mouse_obj = NVDAObjects.NVDAObject.objectFromPoint(current_pos[0], current_pos[1])
            except Exception as e:
                mouse_obj = None
                
            # Log IAccessible and IAccessible2 attributes for debugging
            if mouse_obj:
                # Gets unique id for object under mouse
                mouse_id = self._get_object_id(mouse_obj)
                
                # Check previous object with lock
                with self.curr_obj_lock:
                    prev_obj = self.curr_obj
                    prev_obj_id = self.curr_obj_id
                
                # If there is a valid previous object, compare by ID
                if prev_obj:
                    if mouse_id != prev_obj_id:
                        # Call enter/leave handlers for object change
                        for handler in self.objectHandlers.handlers:
                            if handler.matches(prev_obj):
                                handler.handle_event('leave', prev_obj)
                        for handler in self.objectHandlers.handlers:
                            if handler.matches(mouse_obj):
                                handler.handle_event('enter', mouse_obj)
                
                # Update current object and ID with lock
                with self.curr_obj_lock:
                    self.curr_obj = mouse_obj
                    self.curr_obj_id = mouse_id

class DepthLayer(RenderLayer):
    pass

class TextureLayer(RenderLayer):
    pass