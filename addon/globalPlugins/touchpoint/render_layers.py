import threading
from .dependencies import np, cv2
from .utils import logMessage
import time
from collections import namedtuple

# Region definition for consistent region management
Region = namedtuple('Region', ['left', 'top', 'width', 'height'])

class Renderer:
    """Base class for layer renderers."""
    
    def __call__(self):
        """Execute the renderer.
        
        Layer references are stored in the renderer instance.
        """
        raise NotImplementedError("Renderer subclasses must implement __call__")
    
    def set_plugin(self, plugin):
        """Set the plugin reference if needed."""
        pass

class CaptureRenderer(Renderer):
    """Renderer that captures screen region and updates layer image."""
    
    def __init__(self, layer):
        """Initialize with layer reference.
        
        Args:
            layer: RenderLayer to write captured image to
        """
        self.layer = layer
        self.camera = None
        
    def _init_camera(self):
        """Initialize mss camera if not already done."""
        if self.camera is None:
            try:
                import mss
                self.camera = mss.mss()
            except Exception as e:
                logMessage(f"[ERROR] Failed to initialize mss: {e}")
                
    def __call__(self):
        """Capture screen region and update layer image."""
        self._init_camera()
        if self.camera is None:
            return
            
        try:
            # Get region bounding box
            left, top, right, bottom = self.layer.get_screen_region()
            
            # mss expects a dict with left, top, width, height
            monitor = {
                "left": left,
                "top": top,
                "width": right - left,
                "height": bottom - top
            }
            
            screenshot = self.camera.grab(monitor)
            if screenshot is None:
                return
            
            # Convert mss screenshot to numpy array
            frame = np.array(screenshot)
            
            # mss returns BGRA, extract BGR channels
            frame = frame[:, :, :3]
            
            self.layer.update_image(frame)
        except Exception as e:
            logMessage(f"[ERROR] CaptureRenderer failed: {e}")

class DepthRenderer(Renderer):
    """Renderer that computes depth map from capture layer and writes to depth layer."""
    
    def __init__(self, capture_layer, depth_layer):
        """Initialize with references to capture and depth layers.
        
        Args:
            capture_layer: CaptureLayer to read from
            depth_layer: DepthLayer to write to
        """
        self.capture_layer = capture_layer
        self.depth_layer = depth_layer
        
    def __call__(self):
        """Compute depth map from capture layer and write to depth layer."""
        try:
            # Read capture image with lock
            with self.capture_layer.image_lock:
                if self.capture_layer.image.size == 0:
                    return
                capture_img = self.capture_layer.image.copy()
            
            # Simple depth map: convert to grayscale and normalize
            # In a real implementation, this would use stereo vision or other depth estimation
            gray = cv2.cvtColor(capture_img, cv2.COLOR_BGR2GRAY)
            
            # Normalize to 0-255 range (treating brightness as depth)
            depth_map = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX, dtype=cv2.CV_8U)
            
            # Convert to 3-channel for consistency
            depth_map_3ch = cv2.cvtColor(depth_map, cv2.COLOR_GRAY2BGR)
            
            # Write to depth layer
            self.depth_layer.update_image(depth_map_3ch)
        except Exception as e:
            logMessage(f"[ERROR] DepthWriterRenderer failed: {e}")

class ElevationRenderer(Renderer):
    """Renderer that reads center pixel from depth layer and sets hardware elevation."""
    
    def __init__(self, depth_layer):
        """Initialize with references to depth layer.
        
        Args:
            depth_layer: RenderLayer to read depth from
        """
        self.depth_layer = depth_layer
        self.plugin = None
    
    def set_plugin(self, plugin):
        """Set the plugin reference for hardware access."""
        self.plugin = plugin
        
    def __call__(self):
        """Read center pixel depth and set elevation."""
        try:
            # Read depth image with lock
            with self.depth_layer.image_lock:
                if self.depth_layer.image.size == 0:
                    return
                depth_img = self.depth_layer.image.copy()
            
            # Get center pixel
            height, width = depth_img.shape[:2]
            if height == 0 or width == 0:
                return
                
            center_y = height // 2
            center_x = width // 2
            
            # Read grayscale value from center pixel
            center_value = depth_img[center_y, center_x, 0]  # All channels are same in grayscale
            
            # Normalize to 0.0-1.0 range for elevation
            elevation = center_value / 255.0
            
            # Send elevation to hardware (non-priority so it can be overridden)
            if self.plugin and self.plugin.hardware:
                self.plugin.hardware.send_elevation(elevation, priority=False)
        except Exception as e:
            logMessage(f"[ERROR] ElevationRenderer failed: {e}")

class LayerManager:
    """Class to manage multiple render layers"""
    
    def __init__(self, plugin, layers=None):
        self.plugin = plugin
        self.layers = []  # Changed from OrderedDict to simple list
        if layers is not None:
            for layer in layers:
                self.add_layer(layer)
        
    def add_layer(self, layer):
        """Add a new layer to the manager."""
        self.layers.append(layer)  # Append to list instead of keying by id
        layer.set_plugin(self.plugin)
    
    def populate(self, layer_list):
        """Populate layers from a given list."""
        for layer in layer_list:
            self.add_layer(layer)
            
class RenderLayer:
    """Simple data container for render layers."""
    
    def __init__(self):
        self.plugin = None
        
        # Render image
        self.image = np.array([])  # Placeholder for the rendered image data
        # Lock for synchronizing access to the image
        self.image_lock = threading.Lock()
    
    def update_image(self, new_image):
        """Update the rendered image with thread safety.
        """
        with self.image_lock:
            self.image = new_image
        
    def set_plugin(self, plugin):
        """Set the parent plugin for this layer."""
        self.plugin = plugin
    
    def update_region_size(self, region):
        """Update the layer based on change in region size.
        
        Args:
            region: Region namedtuple with (left, top, width, height)
        """
        with self.image_lock:
            # Saves current image
            oldImage = self.image.copy()
            # Creates blank image with new region size
            self.update_image(np.zeros((region.height, region.width, 3), dtype=np.uint8))
            
            # Gets cropped size of old image to copy over
            crop_width = min(oldImage.shape[1], region.width)
            crop_height = min(oldImage.shape[0], region.height)
            
            # Copies centered cropped portion of old image to new image
            if crop_width > 0 and crop_height > 0:
                x_offset = (region.width - crop_width) // 2
                y_offset = (region.height - crop_height) // 2
                self.image[y_offset:y_offset+crop_height, x_offset:x_offset+crop_width] = oldImage[:crop_height, :crop_width]
            
    def get_screen_region(self):
        """Get the absolute screen region for this layer as (left, top, right, bottom).
        
        Returns:
            tuple: (left, top, right, bottom) in screen coordinates
        """
        
        mouse_x, mouse_y = self.plugin.get_mouse_position()
        
        # Center region on mouse position
        half_width = self.image.shape[1] // 2
        half_height = self.image.shape[0] // 2
        
        left = max(0, mouse_x - half_width)
        top = max(0, mouse_y - half_height)
        
        # Clamp to screen boundaries
        screen_width, screen_height = self.plugin.get_screen_size()
        left = min(left, screen_width - self.image.shape[1])
        top = min(top, screen_height - self.image.shape[0])
        
        right = left + self.image.shape[1]
        bottom = top + self.image.shape[0]
        
        return (left, top, right, bottom)