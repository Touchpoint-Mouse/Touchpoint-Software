import threading
from .dependencies import np, cv2
from .utils import logMessage
import time
from collections import namedtuple

# Region definition for consistent region management
Region = namedtuple('Region', ['left', 'top', 'width', 'height'])

class Renderer:
    """Base class for layer renderers."""
    def __init__(self):
        self.plugin = None
        
    def initialize(self):
        """Optional initialization method for renderers."""
        pass
    
    def __call__(self):
        """Execute the renderer.
        
        Layer references are stored in the renderer instance.
        """
        raise NotImplementedError("Renderer subclasses must implement __call__")
    
    def set_plugin(self, plugin):
        """Set the plugin reference if needed."""
        self.plugin = plugin

class CaptureRenderer(Renderer):
    """Renderer that captures screen region and updates layer image."""
    
    def __init__(self, layer):
        """Initialize with layer reference.
        
        Args:
            layer: RenderLayer to write captured image to
        """
        self.layer = layer
        self.enabled = False
        self.camera_thread = None
        
    def initialize(self):
        """Start camera thread. mss instance is created inside the thread."""
        self.enabled = True
        self.camera_thread = threading.Thread(target=self._camera_thread, daemon=True)
        self.camera_thread.start()
    
    def __call__(self):
        """No-op for CaptureRenderer since capture runs in separate thread."""
        pass
    
    def terminate(self):
        """Terminate the camera thread and release resources."""
        self.enabled = False

    def _camera_thread(self):
        """Thread to continuously capture screen region and update layer image.
        
        Creates mss instance in this thread since mss uses thread-local storage.
        """
        # Create mss instance in this thread (required due to thread-local storage)
        try:
            import mss
            camera = mss.mss()
        except Exception as e:
            logMessage(f"[ERROR] Failed to initialize mss in camera thread: {e}")
            return
        
        while self.enabled:
            if camera is None:
                return
                
            try:
                # Get region bounding box
                left, top, width, height = self.layer.get_screen_region()
                
                # Get screen size
                screen_width, screen_height = self.plugin.get_screen_size()
                
                # Clamps region to screen size
                monitor = {
                    "left": max(left, 0),
                    "top": max(top, 0),
                    "width": min(width, screen_width - left),
                    "height": min(height, screen_height - top)
                }
                
                screenshot = camera.grab(monitor)
                if screenshot is None:
                    return
                
                # Convert mss screenshot to numpy array
                frame = np.array(screenshot)
                
                # mss returns BGRA, extract BGR channels
                frame = frame[:, :, :3]
                
                # Creates empty array with the current image size
                new_image = np.zeros((height, width, 3), dtype=np.uint8)
                
                # Calculate offsets for placing the captured frame in the new image
                x_start = max(0, -left)
                y_start = max(0, -top)
                x_end = x_start + monitor["width"]
                y_end = y_start + monitor["height"]
                
                new_image[y_start:y_end, x_start:x_end] = frame
                
                self.layer.update_image(new_image)
            except Exception as e:
                logMessage(f"[ERROR] CaptureRenderer failed: {e}")
                time.sleep(0.01)  # Small delay to prevent excessive CPU usage

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
            capture_img = self.capture_layer.get_image()
            if capture_img.size == 0:
                return
            
            # Simple depth map: convert to grayscale and normalize
            # In a real implementation, this would use stereo vision or other depth estimation
            gray = cv2.cvtColor(capture_img, cv2.COLOR_BGR2GRAY)
            
            # Normalize to 0-max elevation range (treating brightness as depth)
            depth_map = cv2.normalize(gray, None, 0, self.plugin.hardware.get_max_elevation(), cv2.NORM_MINMAX, dtype=cv2.CV_8U)
            
            # Write to depth layer
            self.depth_layer.update_image(depth_map)
        except Exception as e:
            logMessage(f"[ERROR] DepthRenderer failed: {e}")

class ElevationRenderer(Renderer):
    """Renderer that reads center pixel from depth layer and sets hardware elevation."""
    
    def __init__(self, depth_layer, priority=1):
        """Initialize with references to depth layer.
        
        Args:
            depth_layer: RenderLayer to read depth from
            priority: Priority level for elevation setting (0 = highest)
        """
        self.depth_layer = depth_layer
        self.plugin = None
        self.priority = priority
    
    def set_plugin(self, plugin):
        """Set the plugin reference for hardware access."""
        self.plugin = plugin
        
    def __call__(self):
        """Read center pixel depth and set elevation."""
        try:
            # Read depth image with lock
            depth_img = self.depth_layer.get_image()
            if depth_img.size == 0:
                return
            
            # Get center pixel
            height, width = depth_img.shape[:2]
            if height == 0 or width == 0:
                return
                
            center_y = height // 2
            center_x = width // 2
            
            # Read value from center pixel
            center_value = depth_img[center_y, center_x]  # All channels are same in grayscale
            
            # Send elevation to hardware (non-priority so it can be overridden)
            if self.plugin and self.plugin.hardware:
                self.plugin.hardware.set_global_elevation(center_value, priority=self.priority)
        except Exception as e:
            logMessage(f"[ERROR] ElevationRenderer failed: {e}")
            
class RenderLayer:
    """Simple data container for render layers."""
    
    def __init__(self, id, dtype=np.uint8):
        self.plugin = None
        self.id = id
        
        # Render image
        self.image = np.array([], dtype=dtype)  # Placeholder for the rendered image data
        # Lock for synchronizing access to the image
        self.image_lock = threading.Lock()
        
    def get_image(self):
        """Get the current rendered image with thread safety."""
        with self.image_lock:
            return self.image.copy()
    
    def update_image(self, new_image):
        """Update the rendered image with thread safety.
        """
        with self.image_lock:
            self.image = new_image.copy()
        
    def set_plugin(self, plugin):
        """Set the parent plugin for this layer."""
        self.plugin = plugin
    
    def update_region_size(self, region):
        """Update the layer based on change in region size.
        
        Args:
            region: Region namedtuple with (left, top, width, height)
        """
        # Saves current image
        oldImage = self.get_image()
            
        # Preserve dtype from old image
        dtype = oldImage.dtype
        
        # Creates blank image with new region size and same dtype
        new_image = np.zeros((region.height, region.width, 3), dtype=dtype)
        
        if (oldImage.size > 0):
            # Gets cropped size of old image to copy over
            crop_width = min(oldImage.shape[1], region.width)
            crop_height = min(oldImage.shape[0], region.height)
            
            # Copies centered cropped portion of old image to new image
            if crop_width > 0 and crop_height > 0:
                x_offset = (region.width - crop_width) // 2
                y_offset = (region.height - crop_height) // 2
                new_image[y_offset:y_offset+crop_height, x_offset:x_offset+crop_width] = oldImage[:crop_height, :crop_width]
            
        # Update image with new image
        self.update_image(new_image)
    
    def get_screen_region(self):
        """Get the absolute screen region for this layer as (left, top, width, height).
        
        Returns:
            tuple: (left, top, width, height) in screen coordinates
        """
        
        mouse_x, mouse_y = self.plugin.get_mouse_position()
        
        # Center region on mouse position
        half_width = self.image.shape[1] // 2
        half_height = self.image.shape[0] // 2
        
        left = mouse_x - half_width
        top = mouse_y - half_height
        
        width = self.image.shape[1]
        height = self.image.shape[0]
        
        return Region(left, top, width, height)