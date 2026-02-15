import math
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
                
class ObjectRenderer(Renderer):
    """Renderer that finds unique NVDA objects using a mesh grid and updates layer image with object labels."""
    def __init__(self, capture_layer, object_layer):
        """Initialize with layer reference.
        
        Args:
            capture_layer: CaptureLayer to read from
            object_layer: SemanticLayer to write object labels to
        """
        self.capture_layer = capture_layer
        self.object_layer = object_layer
        
    def __call__(self):
        """Find unique NVDA objects using a mesh grid and update layer image with object labels."""
        # Get capture layer difference mask to determine which regions have changed
        diff_mask = self.capture_layer.get_diff_mask()
        if np.all(diff_mask == False):
            return  # No changes, skip processing
        
        # Iterate through current semantic labels and check whether location boundaries intersect with changed regions in capture layer
        # If so, update semantic layer labels for those regions
        for label, info in self.object_layer.semantic_map.items():
            pass
            
        # Get hardware texture resolution and convert to mesh dimensions
        texture_resolution = self.plugin.hardware.texture_resolution
        aspect_ratio = self.capture_layer.image.shape[1] / self.capture_layer.image.shape[0]
        mesh_height = int(self.capture_layer.image.shape[0] / math.sqrt(texture_resolution/aspect_ratio))
        mesh_width = int(self.capture_layer.image.shape[1] / mesh_height*aspect_ratio)

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
    
    def __init__(self, id, dtype=np.uint8, constant_size=False):
        self.plugin = None
        self.id = id
        self.constant_size = constant_size  # If True, image maintains fixed dimensions
        
        # Render image
        self.image = np.array([], dtype=dtype)  # Placeholder for the rendered image data
        # Previous render image for diffing
        self.prev_image = np.array([], dtype=dtype)
        # Lock for synchronizing access to the image
        self.image_lock = threading.Lock()
        
        # Region tracking for relative bounds
        self.current_region = Region(0, 0, 0, 0)  # Current absolute screen region
        self.prev_region = Region(0, 0, 0, 0)  # Previous absolute screen region
        
    def get_image(self):
        """Get the current rendered image with thread safety."""
        with self.image_lock:
            return self.image.copy()
        
    def get_diff_mask(self):
        """Calculate and return the difference mask with thread safety.
        
        Compares current image with previous image, taking into account region offset.
        Returns a boolean array where True indicates pixels that have changed or are
        in non-overlapping regions.
        
        Returns:
            Boolean array where True indicates pixels that need updating.
        """
        with self.image_lock:
            # If no current image, return empty mask
            if self.image.size == 0:
                return np.array([], dtype=bool)
            
            # Get image dimensions
            if len(self.image.shape) >= 2:
                img_height, img_width = self.image.shape[:2]
            else:
                return np.ones((0, 0), dtype=bool)
            
            # If no previous image or regions not initialized, all pixels are "changed"
            if self.prev_image.size == 0 or self.prev_region.width == 0 or self.current_region.width == 0:
                return np.ones((img_height, img_width), dtype=bool)
            
            # Calculate relative offset between current and previous regions
            dx = self.current_region.left - self.prev_region.left
            dy = self.current_region.top - self.prev_region.top
            
            # Initialize diff mask (all True = all pixels are "changed")
            diff_mask = np.ones((img_height, img_width), dtype=bool)
            
            # Calculate overlapping region to compare
            # Source region in previous image (what to compare from)
            src_x_start = max(0, -dx)
            src_y_start = max(0, -dy)
            src_x_end = min(self.prev_image.shape[1], self.prev_image.shape[1] - dx)
            src_y_end = min(self.prev_image.shape[0], self.prev_image.shape[0] - dy)
            
            # Destination region in current image (what to compare to)
            dst_x_start = max(0, dx)
            dst_y_start = max(0, dy)
            dst_x_end = min(img_width, img_width if dx >= 0 else img_width + dx)
            dst_y_end = min(img_height, img_height if dy >= 0 else img_height + dy)
            
            # Ensure bounds are valid
            src_width = src_x_end - src_x_start
            src_height = src_y_end - src_y_start
            dst_width = dst_x_end - dst_x_start
            dst_height = dst_y_end - dst_y_start
            
            # Compare width/height is the minimum of source and destination
            compare_width = min(src_width, dst_width)
            compare_height = min(src_height, dst_height)
            
            if compare_width > 0 and compare_height > 0:
                # Get overlapping regions from both images
                prev_overlap = self.prev_image[src_y_start:src_y_start+compare_height,
                                               src_x_start:src_x_start+compare_width]
                curr_overlap = self.image[dst_y_start:dst_y_start+compare_height,
                                          dst_x_start:dst_x_start+compare_width]
                
                # Compare pixels - check if any channel differs
                if len(self.image.shape) > 2:  # Multi-channel image
                    pixel_diff = np.any(prev_overlap != curr_overlap, axis=2)
                else:  # Single-channel image
                    pixel_diff = prev_overlap != curr_overlap
                
                # Update diff mask for overlapping region
                diff_mask[dst_y_start:dst_y_start+compare_height,
                         dst_x_start:dst_x_start+compare_width] = pixel_diff
            
            return diff_mask.copy()
    
    def update_image(self, new_image):
        """Update the rendered image with thread safety."""
        with self.image_lock:
            self.image = new_image.copy()
            
    def cycle_state(self):
        """Update the previous image and region to the current ones."""
        with self.image_lock:
            self.prev_image = self.image.copy()
            self.prev_region = self.current_region
        
    def set_plugin(self, plugin):
        """Set the parent plugin for this layer."""
        self.plugin = plugin
    
    def update_region_bounds(self, new_region):
        """Update the layer based on new region bounds (position and size).
        
        Tracks relative offset from previous region and copies overlapping pixels.
        If constant_size is True, maintains fixed image dimensions.
        
        Args:
            new_region: Region namedtuple with (left, top, width, height) in screen coordinates
        """
        with self.image_lock:
            # Store old state
            old_image = self.image.copy() if self.image.size > 0 else None
            old_region = self.current_region
            
            # Update current region
            self.current_region = new_region
            
            # Determine target image size
            if self.constant_size and old_image is not None and old_image.size > 0:
                # Maintain constant size
                target_height, target_width = old_image.shape[:2]
            else:
                # Use new region size
                target_width = new_region.width
                target_height = new_region.height
            
            # Initialize new image and difference mask
            if old_image is not None and old_image.size > 0:
                dtype = old_image.dtype
                num_channels = old_image.shape[2] if len(old_image.shape) > 2 else 1
            else:
                dtype = self.image.dtype if self.image.size > 0 else np.uint8
                num_channels = 3
            
            if num_channels > 1:
                new_image = np.zeros((target_height, target_width, num_channels), dtype=dtype)
            else:
                new_image = np.zeros((target_height, target_width), dtype=dtype)
            
            # If we have an old image, copy overlapping region
            if old_image is not None and old_image.size > 0:
                # Calculate relative offset (how much the region moved)
                dx = new_region.left - old_region.left
                dy = new_region.top - old_region.top
                
                # Calculate overlapping region in old image coordinates
                
                # Source region in old image (what to copy from)
                src_x_start = max(0, -dx)
                src_y_start = max(0, -dy)
                src_x_end = min(old_image.shape[1], old_image.shape[1] - dx)
                src_y_end = min(old_image.shape[0], old_image.shape[0] - dy)
                
                # Destination region in new image (where to copy to)
                dst_x_start = max(0, dx)
                dst_y_start = max(0, dy)
                dst_x_end = min(target_width, target_width if dx >= 0 else target_width + dx)
                dst_y_end = min(target_height, target_height if dy >= 0 else target_height + dy)
                
                # Ensure bounds are valid
                src_width = src_x_end - src_x_start
                src_height = src_y_end - src_y_start
                dst_width = dst_x_end - dst_x_start
                dst_height = dst_y_end - dst_y_start
                
                # Copy width/height is the minimum of source and destination
                copy_width = min(src_width, dst_width)
                copy_height = min(src_height, dst_height)
                
                if copy_width > 0 and copy_height > 0:
                    # Copy overlapping region
                    new_image[dst_y_start:dst_y_start+copy_height, 
                             dst_x_start:dst_x_start+copy_width] = \
                        old_image[src_y_start:src_y_start+copy_height,
                                 src_x_start:src_x_start+copy_width]
            
            # Update image
            self.image = new_image
    
    def get_screen_region(self):
        """Get the absolute screen region for this layer as (left, top, width, height).
        
        Returns the current region if constant_size is True, otherwise calculates
        a new region centered on the mouse.
        
        Returns:
            Region: (left, top, width, height) in screen coordinates
        """
        with self.image_lock:
            if self.constant_size and self.current_region.width > 0:
                # Return the stored current region for constant size layers
                return self.current_region
            
            # Calculate new region centered on mouse
            mouse_x, mouse_y = self.plugin.get_mouse_position()
            
            # Get image dimensions safely
            if self.image.size > 0:
                if len(self.image.shape) >= 2:
                    height, width = self.image.shape[:2]
                else:
                    width = height = 0
            else:
                width = height = 0
            
            if width == 0 or height == 0:
                # No valid image, use default or plugin capture region size
                if self.plugin:
                    width = self.plugin.capture_region_width
                    height = self.plugin.capture_region_height
                else:
                    width = height = 100
            
            # Center region on mouse position
            half_width = width // 2
            half_height = height // 2
            
            left = mouse_x - half_width
            top = mouse_y - half_height
            
            return Region(left, top, width, height)
    
class SemanticLayer(RenderLayer):
    """Render layer that stores semantic segmentation labels for each pixel."""
    def __init__(self, id, constant_size=True):
        super().__init__(id, dtype=np.uint8, constant_size=constant_size)
        # Map for semantic information based on pixel value
        self.semantic_map = {}
        