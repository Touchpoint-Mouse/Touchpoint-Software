import math
import threading
from .dependencies import np, cv2
from .utils import logMessage
import time
from collections import namedtuple
import NVDAObjects

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
                # Get region from layer's current region
                region = self.layer.current_region
                left, top, width, height = region.left, region.top, region.width, region.height
                
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
    
    def _is_duplicate(self, obj, existing_objects):
        """Check if an object is a duplicate based on bounding box match.
        
        Args:
            obj: NVDA object to check
            existing_objects: List of objects already detected
        Returns:
            bool: True if object is a duplicate, False otherwise
        """
        try:
            if not hasattr(obj, 'location') or obj.location is None:
                return False
                
            obj_location = obj.location
            
            for obj_info in existing_objects:
                existing_location = obj_info.get('location')
                if existing_location is None:
                    continue
                    
                if (existing_location.left == obj_location.left and
                    existing_location.top == obj_location.top and
                    existing_location.width == obj_location.width and
                    existing_location.height == obj_location.height):
                    return True  # Exact match of bounding box
                    
        except Exception as e:
            logMessage(f"[ERROR] Duplicate checking failed: {e}")
            
        return False
        
    def __call__(self):
        """Find unique NVDA objects using a mesh grid and update layer image with object labels."""
        try:
            # Save current object layer image at the beginning
            current_image = self.object_layer.get_image()
            
            # Get capture layer difference mask to determine which regions have changed
            diff_mask = self.capture_layer.get_diff_mask()
            if diff_mask.size == 0 or np.all(diff_mask == False):
                return  # No changes, skip processing
            
            # Get current capture region
            region = self.capture_layer.current_region
            
            # Get object mesh dimensions from config
            if not self.plugin or not hasattr(self.plugin, 'config'):
                return
                
            mesh_dims = self.plugin.config.layer_dimensions.get('object_mesh')
            if mesh_dims is None:
                return
                
            mesh_width, mesh_height = mesh_dims
            
            # Work on a copy of the image
            new_image = current_image.copy()
            
            # Invalidate semantic labels that intersect with changed regions
            labels_to_remove = []
            for label, obj_info in self.object_layer.semantic_map.items():
                location = obj_info.get('location')
                if location is None:
                    continue
                
                # Convert object location to capture-layer-relative coordinates
                obj_left = location.left - region.left
                obj_top = location.top - region.top
                obj_right = obj_left + location.width
                obj_bottom = obj_top + location.height
                
                # Check if object bounding box intersects with changed regions
                # Clamp to image bounds
                img_height, img_width = diff_mask.shape[:2]
                obj_left = max(0, min(obj_left, img_width))
                obj_top = max(0, min(obj_top, img_height))
                obj_right = max(0, min(obj_right, img_width))
                obj_bottom = max(0, min(obj_bottom, img_height))
                
                # Check if any pixels in the object region have changed
                if obj_right > obj_left and obj_bottom > obj_top:
                    object_region_mask = diff_mask[int(obj_top):int(obj_bottom), int(obj_left):int(obj_right)]
                    if object_region_mask.size > 0 and np.any(object_region_mask):
                        labels_to_remove.append(label)
            
            # Remove invalidated labels from semantic map and clear from working image
            for label in labels_to_remove:
                self.object_layer.remove_label(label)
            
            # Create mesh grid for object detection
            # Generate evenly spaced points across the capture region (excluding endpoints to stay in bounds)
            x_points = np.linspace(region.left, region.left + region.width - 1, mesh_width, dtype=int)
            y_points = np.linspace(region.top, region.top + region.height - 1, mesh_height, dtype=int)
            
            # Get list of existing objects for duplicate checking
            existing_objects = list(self.object_layer.semantic_map.values())
            
            # Loop through mesh grid and detect unique objects
            for y in y_points:
                for x in x_points:
                    # Continue if this point already has a label in the working image (skip to next point)
                    y_rel = y - region.top
                    x_rel = x - region.left
                    if new_image[y_rel, x_rel] != 0:
                        continue
                    try:
                        # Get NVDA object at this screen position
                        obj = NVDAObjects.NVDAObject.objectFromPoint(int(x), int(y))
                        
                        # Skip if no object or object has no location (can't label it)
                        if obj is None or not hasattr(obj, 'location') or obj.location is None:
                            continue
                        
                        # Skip if any of the object's dimensions are smaller than the mesh grid cell size (to avoid labeling tiny objects that won't be reliably detected)
                        if obj.location.width < region.width / mesh_width or obj.location.height < region.height / mesh_height:
                            continue
                        
                        # Check if this object is a duplicate of an already known object
                        if self._is_duplicate(obj, existing_objects):
                            # Duplicate of existing object - skip it
                            continue
                        
                        # Add to semantic map
                        obj_info = {
                            'name': obj.name if hasattr(obj, 'name') else 'Unknown',
                            'role': obj.role if hasattr(obj, 'role') else None,
                            'location': obj.location if hasattr(obj, 'location') else None,
                            'object': obj  # Keep reference for handlers
                        }
                        existing_objects.append(obj_info)
                        new_label = self.object_layer.add_label(obj_info)
                        
                        # Fill object region with label immediately
                        if hasattr(obj, 'location') and obj.location is not None:
                            location = obj.location
                            
                            # Convert to capture-layer-relative coordinates
                            obj_left = location.left - region.left
                            obj_top = location.top - region.top
                            obj_width = location.width
                            obj_height = location.height
                            
                            # Clamp to image bounds
                            img_height, img_width = new_image.shape[:2]
                            x1 = max(0, min(int(obj_left), img_width))
                            y1 = max(0, min(int(obj_top), img_height))
                            x2 = max(0, min(int(obj_left + obj_width), img_width))
                            y2 = max(0, min(int(obj_top + obj_height), img_height))
                            
                            # Fill object region with label
                            if x2 > x1 and y2 > y1:
                                new_image[y1:y2, x1:x2] = new_label
                                
                    except Exception as e:
                        # Silently skip points that fail - common for invalid screen positions
                        pass
            
            # Update the layer image atomically at the end
            self.object_layer.update_image(new_image)
                        
        except Exception as e:
            logMessage(f"[ERROR] ObjectRenderer failed: {e}")
        

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
            
            # Get padding from config
            padding = self.plugin.config.capture_padding
            
            # Crop to hardware area (remove padding on all sides)
            if padding > 0:
                h, w = capture_img.shape[:2]
                capture_img = capture_img[padding:h-padding, padding:w-padding]
            
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
    
    def __init__(self, id, dtype=np.uint8, constant_size=None):
        self.plugin = None
        self.id = id
        # constant_size can be:
        # - None or False: Dynamic size following region bounds
        # - Tuple (width, height): Fixed dimensions in pixels
        self.constant_size = constant_size
        
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
        """Update the rendered image with thread safety.
        
        Automatically resizes image for constant_size layers using cv2.resize.
        
        Args:
            new_image: New image to set (will be resized if constant_size is set)
        """
        with self.image_lock:
            # If constant_size is set, resize the image to match
            if self.constant_size:
                target_width, target_height = self.constant_size
                # Check if resize is needed
                if new_image.shape[:2] != (target_height, target_width):
                    # Resize using cv2 (import from dependencies)
                    from .dependencies import cv2
                    new_image = cv2.resize(new_image, (target_width, target_height), interpolation=cv2.INTER_LINEAR)
            
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
        If constant_size is set to (width, height), maintains those fixed dimensions.
        
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
            if self.constant_size:
                # Use constant size (width, height tuple)
                target_width, target_height = self.constant_size
            elif old_image is not None and old_image.size > 0:
                # For dynamic layers that already have an image, maintain size if it exists
                # (This case shouldn't normally happen, but we handle it gracefully)
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
    
class SemanticLayer(RenderLayer):
    """Render layer that stores semantic segmentation labels for each pixel."""
    def __init__(self, id, constant_size=None):
        super().__init__(id, dtype=np.uint8, constant_size=constant_size)
        # Map for semantic information based on pixel value
        self.semantic_map = {}
        # Next label to assign for new objects (start from 1 since 0 is background)
        self.next_label = 1
        
    def remove_label(self, label):
        """Remove a label from the semantic map."""
        if label in self.semantic_map:
            del self.semantic_map[label]
            with self.image_lock:
                # Clear pixels with this label (set to 0) in working image
                self.image[self.image == label] = 0
    
    def add_label(self, obj_info):
        """Add a new object label to the semantic map.
        
        Args:
            obj_info: Dictionary containing object information (name, role, location, etc.)
        Returns:
            int: The label assigned to this object
        """
        label = self.next_label
        self.semantic_map[label] = obj_info
        self.next_label += 1
        return label
        