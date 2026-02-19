import math
import threading
from .dependencies import np, cv2
from .utils import Rect, logMessage
import time
from collections import namedtuple
import NVDAObjects

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
        self.capture_image = None
        self.capture_region = None  # Store region that corresponds to captured image
        self.capture_lock = threading.Lock()
        
    def initialize(self):
        """Start camera thread. mss instance is created inside the thread."""
        self.enabled = True
        self.camera_thread = threading.Thread(target=self._camera_thread, daemon=True)
        self.camera_thread.start()
    
    def __call__(self):
        """Update the layer image by cropping from the large captured buffer to the desired region."""
        with self.capture_lock:
            if self.capture_image is None or self.capture_region is None:
                return
            
            large_capture = self.capture_image
            large_region = self.capture_region
        
        # Get the desired region (set by update_region_bounds)
        desired_region = self.layer.current_region
        
        # Calculate crop bounds: where desired_region sits within large_region
        crop_x = desired_region.left - large_region.left
        crop_y = desired_region.top - large_region.top
        crop_x_end = crop_x + desired_region.width
        crop_y_end = crop_y + desired_region.height
        
        # Clamp crop bounds to large capture dimensions
        crop_x = max(0, min(crop_x, large_capture.shape[1]))
        crop_y = max(0, min(crop_y, large_capture.shape[0]))
        crop_x_end = max(0, min(crop_x_end, large_capture.shape[1]))
        crop_y_end = max(0, min(crop_y_end, large_capture.shape[0]))
        
        # Crop the image
        if crop_x < crop_x_end and crop_y < crop_y_end:
            cropped_image = large_capture[crop_y:crop_y_end, crop_x:crop_x_end]
            self.layer.update_image(cropped_image)
    
    def terminate(self):
        """Terminate the camera thread and release resources."""
        self.enabled = False

    def _camera_thread(self):
        """Thread to continuously capture a large screen region with padding.
        
        Captures a larger buffer than needed, so __call__() can crop to the exact region
        requested by update_region_bounds. Creates mss instance in this thread since 
        mss uses thread-local storage.
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
                # Get desired region and add padding for larger capture buffer
                region = self.layer.current_region
                
                # Get padding from config (default to 100 pixels if not set)
                padding = 100
                if self.plugin and hasattr(self.plugin, 'config'):
                    padding = self.plugin.config.software.get('capture_padding', 100)
                
                # Calculate padded region
                padded_left = region.left - padding
                padded_top = region.top - padding
                padded_width = region.width + 2 * padding
                padded_height = region.height + 2 * padding
                
                # Get screen size
                screen_width, screen_height = self.plugin.get_screen_size()
                
                # Clamp padded region to screen bounds
                monitor = {
                    "left": max(padded_left, 0),
                    "top": max(padded_top, 0),
                    "width": min(padded_width, screen_width - max(padded_left, 0)),
                    "height": min(padded_height, screen_height - max(padded_top, 0))
                }
                
                screenshot = camera.grab(monitor)
                if screenshot is None:
                    return
                
                # Convert mss screenshot to numpy array
                frame = np.array(screenshot)
                
                # mss returns BGRA, extract BGR channels
                captured_image = frame[:, :, :3]
                
                # Store the captured region (not the desired region)
                captured_region = Rect(
                    left=monitor["left"],
                    top=monitor["top"],
                    width=monitor["width"],
                    height=monitor["height"]
                )
                
                # Store both image and the region it corresponds to atomically
                with self.capture_lock:
                    self.capture_image = captured_image
                    self.capture_region = captured_region
            except Exception as e:
                logMessage(f"[ERROR] CaptureRenderer failed: {e}")
            
        time.sleep(self.plugin.config.software['threading']['capture'])  # Small delay to prevent excessive CPU usage
        
class ObjectTreeRenderer(Renderer):
    """Renderer that finds unique NVDA objects using a mesh grid and updates the object tree layer."""
    def __init__(self, capture_layer, object_tree_layer):
        """Initialize with layer reference.
        
        Args:
            capture_layer: CaptureLayer to read from
            object_tree_layer: ObjectTreeLayer to write object labels to
        """
        self.capture_layer = capture_layer
        self.object_tree_layer = object_tree_layer
        self.iou_threshold = 0.8
        
    def __call__(self):
        """Find unique NVDA objects using a mesh grid and update the object tree layer."""
        try:
            # Get capture layer difference mask to determine which regions have changed
            diff_mask = self.capture_layer.get_diff_mask()
            
            if diff_mask.size == 0 or not np.any(diff_mask):
                return  # No changes, skip processing
            
            # Get current capture region
            region = self.capture_layer.current_region
            
            # Get object mesh dimensions from config
            if not self.plugin or not hasattr(self.plugin, 'config'):
                logMessage("[ObjectTreeRenderer] No plugin or config available")
                return
                
            mesh_dims = self.plugin.config.layer_dimensions.get('object_mesh')
            if mesh_dims is None:
                logMessage("[ObjectTreeRenderer] No object_mesh dimensions in config")
                return
                
            mesh_width, mesh_height = mesh_dims
            
            # Invalidate objects whose actual pixels have changed using breadth-first traversal
            # Remove labels immediately during traversal so we skip checking children of removed nodes
            current_object_image = self.object_tree_layer.get_image()
            img_height, img_width = diff_mask.shape[:2]
            labels_removed = 0
            
            # Collect redraw actions during invalidation to execute after mesh grid scan
            # This prevents redraws from filling pixels and blocking mesh grid detection
            redraw_actions = []
            
            # Import occlusion checking function
            from .utils import is_window_occluded
            
            # Traverse all window trees to check for invalidation
            for hwnd, window_tree in list(self.object_tree_layer.window_trees.items()):
                for node in self.object_tree_layer.BreadthFirstIterator(window_tree, skip_root=True):
                    # Skip if this node was already removed as part of a parent's subtree
                    if node.label not in self.object_tree_layer.label_map:
                        continue
                    
                    location = node.obj_info.get('location')
                    if location is None:
                        continue
                    
                    # Check if this object is completely occluded by windows in front
                    if is_window_occluded(hwnd, location):
                        self.object_tree_layer.remove_label(node.label)
                        labels_removed += 1
                        logMessage(f"[ObjectTreeRenderer] Removed occluded label {node.label} from window {hwnd}")
                        continue
                    
                    # Convert object location to capture-layer-relative coordinates
                    obj_left = location.left - region.left
                    obj_top = location.top - region.top
                    obj_right = obj_left + location.width
                    obj_bottom = obj_top + location.height
                    
                    # Clamp to image bounds
                    obj_left_clamped = max(0, obj_left)
                    obj_top_clamped = max(0, obj_top)
                    obj_right_clamped = min(img_width, obj_right)
                    obj_bottom_clamped = min(img_height, obj_bottom)
                    
                    # If clamped region has zero size, object is completely outside bounds
                    # Remove immediately to prevent orphaned pixels
                    if obj_right_clamped <= obj_left_clamped or obj_bottom_clamped <= obj_top_clamped:
                        self.object_tree_layer.remove_label(node.label)
                        labels_removed += 1
                        logMessage(f"[ObjectTreeRenderer] Removed out-of-bounds label {node.label}")
                        continue
                    
                    # Extract the bounding box region from both object layer and diff_mask
                    object_region = current_object_image[obj_top_clamped:obj_bottom_clamped, obj_left_clamped:obj_right_clamped]
                    diff_region = diff_mask[obj_top_clamped:obj_bottom_clamped, obj_left_clamped:obj_right_clamped]
                    
                    # Find pixels that belong to this specific object (where pixel value == label)
                    object_pixels_mask = (object_region == node.label)
                    
                    # Count how many object pixels have changed
                    changed_object_pixels = np.sum(object_pixels_mask & diff_region)
                    total_object_pixels = np.sum(object_pixels_mask)
                    
                    # Only invalidate if significant portion of object pixels changed (>50% threshold)
                    # This prevents invalidation from minor visual updates like highlights, focus indicators, etc.
                    if total_object_pixels > 0:
                        change_ratio = changed_object_pixels / total_object_pixels
                        if change_ratio > 0.50:
                            self.object_tree_layer.remove_label(node.label)
                            labels_removed += 1
                            logMessage(f"[ObjectTreeRenderer] Removed invalidated label {node.label} (changed {change_ratio*100:.1f}%)")
                        else:
                            # Object not invalidated - queue redraw to update visible bounding box
                            # This handles cases where object is at edge and region shifted
                            redraw_actions.append((node.label, location))
                    else:
                        # Object has no current pixels but is now in visible bounds
                        # This happens when object was outside capture region and has now re-entered
                        # Queue redraw to make it visible again
                        redraw_actions.append((node.label, location))
            
            # Get fresh object image after invalidation for mesh grid detection
            # This ensures mesh grid can detect objects where labels were just removed
            current_object_image = self.object_tree_layer.get_image()
            
            # Create mesh grid for object detection
            # Generate evenly spaced points across the capture region including edges
            # linspace includes both endpoints, so edges are covered
            x_points = np.linspace(region.left, region.left + region.width - 1, mesh_width, dtype=int)
            y_points = np.linspace(region.top, region.top + region.height - 1, mesh_height, dtype=int)
            
            # Collect drawing actions during mesh scan to execute at the end
            # This prevents large parent objects from overwriting children during the scan
            
            # Gets screen bounds for occlusion checking during mesh scan
            screen_width, screen_height = self.plugin.get_screen_size()
            
            # Loop through mesh grid and detect unique objects
            for y in y_points:
                for x in x_points:
                    # Checks to ensure global coordinates are within screen bounds
                    if y < 0 or y >= screen_height or x < 0 or x >= screen_width:
                        continue
                    
                    # Continue if this point already has a label in the working image (skip to next point)
                    y_rel = y - region.top
                    x_rel = x - region.left
                    
                    pixel_value = current_object_image[y_rel, x_rel]
                    if pixel_value != 0:
                        continue
                    
                    try:
                        # Get NVDA object at this screen position
                        obj = NVDAObjects.NVDAObject.objectFromPoint(int(x), int(y))
                        
                        # Skip if no object or object has no location (can't label it)
                        if obj is None:
                            continue
                        
                        if not hasattr(obj, 'location') or obj.location is None:
                            continue
                        
                        # Skip if any of the object's dimensions are smaller than the mesh grid cell size
                        cell_width = region.width / mesh_width
                        cell_height = region.height / mesh_height
                        if obj.location.width < cell_width or obj.location.height < cell_height:
                            continue
                        
                        # Create obj_info dictionary
                        obj_info = {
                            'name': obj.name if hasattr(obj, 'name') else 'Unknown',
                            'role': obj.role if hasattr(obj, 'role') else None,
                            'location': obj.location,
                            'object': obj
                        }
                        
                        # Add to tree layer immediately (handles duplicate checking via tree comparison)
                        new_label = self.object_tree_layer.add_label(obj_info)
                        
                        if new_label > 0:
                            # Get role name for logging
                            role_name = "Unknown"
                            try:
                                if 'role' in obj_info and obj_info['role'] is not None:
                                    import controlTypes
                                    role_name = controlTypes.Role(obj_info['role']).name if hasattr(controlTypes, 'Role') else str(obj_info['role'])
                            except:
                                role_name = str(obj_info['role']) if 'role' in obj_info else "Unknown"
                            
                            logMessage(f"[ObjectTreeRenderer] Detected object: label={new_label}, name='{obj_info['name']}', role={role_name}, location=({obj_info['location'].left},{obj_info['location'].top},{obj_info['location'].width},{obj_info['location'].height})")
                            
                            # Queue drawing for later (after mesh scan completes)
                            redraw_actions.append((new_label, obj_info['location']))
                                
                    except Exception as e:
                        pass  # Silently skip exceptions at individual points
            
            # Execute all fill operations at once: redraws from invalidation + new detections
            # This prevents early fills from blocking mesh grid detection
            for label, location in redraw_actions:
                self.object_tree_layer.fill_object_region(label, location, region)
                        
        except Exception as e:
            logMessage(f"[ERROR] ObjectTreeRenderer failed: {e}")
        

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
        # DISABLED: Using ObjectDepthRenderer instead
        pass
        # try:
        #     # Read capture image with lock
        #     capture_img = self.capture_layer.get_image()
        #     if capture_img.size == 0:
        #         return
        #     
        #     # Get padding from config
        #     padding = self.plugin.config.capture_padding
        #     
        #     # Crop to hardware area (remove padding on all sides)
        #     if padding > 0:
        #         h, w = capture_img.shape[:2]
        #         capture_img = capture_img[padding:h-padding, padding:w-padding]
        #     
        #     # Simple depth map: convert to grayscale and normalize
        #     # In a real implementation, this would use stereo vision or other depth estimation
        #     gray = cv2.cvtColor(capture_img, cv2.COLOR_BGR2GRAY)
        #     
        #     # Normalize to 0-max elevation range (treating brightness as depth)
        #     depth_map = cv2.normalize(gray, None, 0, self.plugin.hardware.get_max_elevation(), cv2.NORM_MINMAX, dtype=cv2.CV_8U)
        #     
        #     # Write to depth layer
        #     self.depth_layer.update_image(depth_map)
        # except Exception as e:
        #     logMessage(f"[ERROR] DepthRenderer failed: {e}")

class ObjectDepthRenderer(Renderer):
    """Renderer that reads from object tree layer and writes scaled depth to depth layer."""
    
    def __init__(self, object_tree_layer, depth_layer):
        """Initialize with references to object tree and depth layers.
        
        Args:
            object_tree_layer: ObjectTreeLayer to read object labels from
            depth_layer: DepthLayer to write depth values to
        """
        self.object_tree_layer = object_tree_layer
        self.depth_layer = depth_layer
        
    def __call__(self):
        """Convert object labels to scaled depth values and write to depth layer."""
        try:
            # Get object layer image
            object_img = self.object_tree_layer.get_image()
            if object_img.size == 0:
                return
            
            # Get max elevation from hardware
            max_elevation = self.plugin.hardware.get_max_elevation()
            
            # Create depth map with same dimensions as object layer
            depth_map = np.zeros_like(object_img, dtype=np.uint8)
            
            # Scale object labels to depth values
            # Higher labels (more in front) = higher depth values
            # Label 0 (background) = 0 depth
            # Max label = max_elevation
            
            # Find max label in use
            max_label = np.max(object_img)
            
            if max_label > 0:
                # Scale labels to depth range
                # Use linear scaling: depth = (label / max_label) * max_elevation
                mask = object_img > 0  # Non-background pixels
                depth_map[mask] = ((object_img[mask].astype(np.float32) / max_label) * max_elevation).astype(np.uint8)
            
            # Get padding from config to crop to hardware area
            padding = self.plugin.config.capture_padding
            
            # Crop to hardware area (remove padding on all sides)
            if padding > 0:
                h, w = depth_map.shape[:2]
                if h > 2*padding and w > 2*padding:
                    depth_map = depth_map[padding:h-padding, padding:w-padding]
                else:
                    logMessage(f"[ObjectDepthRenderer] Warning: depth map too small to crop padding {padding}")
            
            # Write to depth layer
            self.depth_layer.update_image(depth_map)
            
        except Exception as e:
            logMessage(f"[ERROR] ObjectDepthRenderer failed: {e}")
            import traceback
            logMessage(traceback.format_exc())

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
    
    def __init__(self, id, dtype=np.uint8, constant_size=None, num_channels=3):
        self.plugin = None
        self.id = id
        # constant_size can be:
        # - None or False: Dynamic size following region bounds
        # - Tuple (width, height): Fixed dimensions in pixels
        self.constant_size = constant_size
        # Number of channels (1 for grayscale, 3 for BGR)
        self.num_channels = num_channels
        
        # Render image
        self.image = np.array([], dtype=dtype)  # Placeholder for the rendered image data
        # Previous render image for diffing
        self.prev_image = np.array([], dtype=dtype)
        
        # Region tracking for relative bounds
        self.current_region = Rect(0, 0, 0, 0)  # Current absolute screen region
        self.prev_region = Rect(0, 0, 0, 0)  # Previous absolute screen region
        
    def get_image(self):
        """Get the current rendered image with thread safety."""
        return self.image.copy()
        
    def get_diff_mask(self):
        """Calculate and return the difference mask with thread safety.
        
        Compares current image with previous image, taking into account region offset.
        Returns a boolean array where True indicates pixels that have changed or are
        in non-overlapping regions.
        
        Returns:
            Boolean array where True indicates pixels that need updating.
        """
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
        
        # Calculate overlapping region to compare (pixels shift opposite to region movement)
        # Source region in previous image (what to compare from)
        src_x_start = max(0, dx)
        src_y_start = max(0, dy)
        src_x_end = min(self.prev_image.shape[1], self.prev_image.shape[1] + dx)
        src_y_end = min(self.prev_image.shape[0], self.prev_image.shape[0] + dy)
        
        # Destination region in current image (what to compare to)
        dst_x_start = max(0, -dx)
        dst_y_start = max(0, -dy)
        dst_x_end = min(img_width, img_width if dx <= 0 else img_width - dx)
        dst_y_end = min(img_height, img_height if dy <= 0 else img_height - dy)
        
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
            new_region: Rect
        """
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
            # Use stored dtype from layer initialization
            dtype = self.image.dtype if hasattr(self, 'image') and self.image.dtype else np.uint8
            # Use stored num_channels from layer initialization
            num_channels = self.num_channels
        
        if num_channels > 1:
            new_image = np.zeros((target_height, target_width, num_channels), dtype=dtype)
        else:
            new_image = np.zeros((target_height, target_width), dtype=dtype)
        
        # If we have an old image, copy overlapping region
        if old_image is not None and old_image.size > 0:
            # Calculate relative offset (how much the region moved)
            dx = new_region.left - old_region.left
            dy = new_region.top - old_region.top
            
            # When region moves right, layer content shifts left (opposite direction)
            # Source region in old image (what to copy from)
            src_x_start = max(0, dx)
            src_y_start = max(0, dy)
            src_x_end = min(old_image.shape[1], old_image.shape[1] + dx)
            src_y_end = min(old_image.shape[0], old_image.shape[0] + dy)
            
            # Destination region in new image (where to copy to)
            dst_x_start = max(0, -dx)
            dst_y_start = max(0, -dy)
            dst_x_end = min(target_width, target_width if dx <= 0 else target_width - dx)
            dst_y_end = min(target_height, target_height if dy <= 0 else target_height - dy)
            
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
        self.image = new_image.copy()
    
class SemanticLayer(RenderLayer):
    """Render layer that stores semantic segmentation labels for each pixel."""
    def __init__(self, id, constant_size=None):
        super().__init__(id, dtype=np.uint8, constant_size=constant_size, num_channels=1)
        # Map for semantic information based on pixel value
        self.semantic_map = {}
        # Next label to assign for new objects (start from 1 since 0 is background)
        self.next_label = 1
        
    def remove_label(self, label):
        """Remove a label from the semantic map."""
        if label in self.semantic_map:
            del self.semantic_map[label]
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
        
        # Wraparound label if we exceed 255 (max for uint8)
        if self.next_label > 255:
            self.next_label = 1
        return label
    
class ObjectTreeLayer(RenderLayer):
    """Render layer that stores hierarchical object tree information with depth-based labels."""
    class Node:
        """Node class for representing objects in a tree structure."""
        def __init__(self, label, obj_info, depth=0):
            self.label = label  # Zero-aligned by depth
            self.obj_info = obj_info
            self.depth = depth
            self.children = []
            self.subtree_size = 1  # Number of nodes in this subtree (including self)
            
        def update_subtree_size(self):
            """Recursively calculate subtree size."""
            self.subtree_size = 1 + sum(child.update_subtree_size() for child in self.children)
            return self.subtree_size
            
        def compare_to(self, other_node, duplicate_threshold=10):
            """Compare this node to another node for tree construction within same window."""
            try:
                if 'location' not in self.obj_info or 'location' not in other_node.obj_info:
                    return 0  # Can't compare without location
                
                loc1 = self.obj_info['location']
                loc2 = other_node.obj_info['location']
                
                left1, top1, right1, bottom1 = loc1.left, loc1.top, loc1.left + loc1.width, loc1.top + loc1.height
                left2, top2, right2, bottom2 = loc2.left, loc2.top, loc2.left + loc2.width, loc2.top + loc2.height
                
                # Check for duplicate (very similar bounding box)
                if (abs(left1 - left2) < duplicate_threshold and abs(top1 - top2) < duplicate_threshold and 
                    abs(right1 - right2) < duplicate_threshold and abs(bottom1 - bottom2) < duplicate_threshold):
                    return None  # Duplicate object
                
                # Check if self is fully inside other (containment)
                if left1 > left2 and top1 > top2 and right1 < right2 and bottom1 < bottom2:
                    return -1  # self is child of other
                
                # Check if self is fully outside other (no overlap)
                if right1 < left2 or left1 > right2 or bottom1 < top2 or top1 > bottom2:
                    return 1  # self is sibling or unrelated
                
                # Objects overlap but neither contains the other - treat as siblings
                return 0
                
            except Exception as e:
                logMessage(f"[ERROR] Node comparison failed: {e}")
                return 0
    
    class BreadthFirstIterator:
        """Iterator for traversing the object tree in breadth-first order."""
        def __init__(self, root, skip_root=False):
            self.queue = [root] if (root and not skip_root) else ([] if not root else list(root.children))
        
        def __iter__(self):
            return self
        
        def __next__(self):
            if not self.queue:
                raise StopIteration
            current = self.queue.pop(0)
            self.queue.extend(current.children)
            return current
            
    def __init__(self, id, constant_size=None):
        super().__init__(id, dtype=np.uint8, constant_size=constant_size, num_channels=1)
        # Each window has its own independent tree
        # Maps window handle (HWND) -> root Node
        self.window_trees = {}
        # Maps window handle -> z-order position (0 = topmost)
        self.window_z_orders = {}
        # Label-to-node mapping for fast lookup
        self.label_map = {}
        
        # Fixed depth-based label allocation
        self.max_depth = 5  # Maximum supported depth levels
        self.labels_per_depth = 50  # Fixed allocation per depth
        self.depth_counters = {}  # depth -> next available label in that depth's range
        
    def add_label(self, obj_info, parent_label=None):
        """Add a new object label to the tree with depth-based label assignment.
        
        Args:
            obj_info: Dictionary containing object information (name, role, location, etc.)
            parent_label: Optional parent label to insert under (None = find automatically)
        Returns:
            int: The label assigned to this object
        """
        from .utils import get_object_window_handle, get_window_z_order
        
        # Get window handle for this object
        obj = obj_info.get('object')
        if not obj:
            return 0
        
        hwnd = get_object_window_handle(obj)
        if not hwnd:
            return 0
        
        # Get or create tree for this window
        if hwnd not in self.window_trees:
            z_order = get_window_z_order(hwnd)
            self.window_trees[hwnd] = self.Node(0, {'name': f'window_{hwnd}', 'hwnd': hwnd}, depth=0)
            self.window_z_orders[hwnd] = z_order if z_order >= 0 else 999
        
        window_tree = self.window_trees[hwnd]
        
        # Create temporary node with placeholder label to find insertion point
        temp_node = self.Node(0, obj_info)
        
        # Find parent node within this window's tree
        if parent_label is not None and parent_label in self.label_map:
            parent_node = self.label_map[parent_label]
        else:
            parent_node = self._find_parent(window_tree, temp_node)
        
        if parent_node is None:
            return 0
        
        # Calculate label based on window z-order and position in tree
        label = self._calculate_label_with_z_order(hwnd, parent_node)
        
        if label == 0:
            return 0
        
        # Create and insert new node
        depth = parent_node.depth + 1
        new_node = self.Node(label, obj_info, depth)
        parent_node.children.append(new_node)
        self.label_map[label] = new_node
        
        # Update subtree sizes for this window's tree
        window_tree.update_subtree_size()
        
        return label
    
    def _find_parent(self, current, new_node):
        """Find the correct parent node for insertion based on bounding box comparison."""
        if current is None:
            return None
        
        # Check if new_node should be a child of any of current's children
        for child in current.children:
            comparison = new_node.compare_to(child)
            if comparison == -1:  # New node is inside this child
                return self._find_parent(child, new_node)
            elif comparison is None:  # Duplicate
                return None
        
        # No child contains new_node, so it should be a child of current
        return current
    
    def _get_depth_range(self, depth):
        """Get the fixed (start, end) label range for a depth level.
        
        Args:
            depth: Depth level (0 = window root, 1+ = UI elements)
        
        Returns:
            Tuple of (start_label, end_label) or None if depth exceeds max
        """
        if depth == 0:
            return (0, 0)  # Window root always gets label 0
        
        if depth > self.max_depth:
            return None  # Depth exceeds maximum
        
        # Fixed ranges: depth 1 = 1-50, depth 2 = 51-100, depth 3 = 101-150, etc.
        start_label = (depth - 1) * self.labels_per_depth + 1
        end_label = depth * self.labels_per_depth
        
        return (start_label, end_label)
    
    def _calculate_label_with_z_order(self, hwnd, parent_node):
        """Allocate label from fixed depth range ensuring children have higher labels than parents.
        
        Each depth has a fixed range of labels:
        - Depth 0 (window roots): 0
        - Depth 1: 1-50
        - Depth 2: 51-100
        - Depth 3: 101-150
        - Depth 4: 151-200
        - Depth 5: 201-250
        
        Returns:
            int: Label for the new node, or 0 if allocation failed
        """
        depth = parent_node.depth + 1
        
        # Get fixed range for this depth
        depth_range = self._get_depth_range(depth)
        if depth_range is None:
            logMessage(f"[ERROR] Depth {depth} exceeds max_depth {self.max_depth}")
            return 0
        
        start_label, end_label = depth_range
        
        # Special case for window root
        if depth == 0:
            return 0
        
        # Initialize counter for this depth if needed
        if depth not in self.depth_counters:
            self.depth_counters[depth] = start_label
        
        # Get next available label from this depth's range
        label = self.depth_counters[depth]
        
        # Check if we've exhausted this depth's range
        if label > end_label:
            logMessage(f"[ERROR] Depth {depth} exhausted its label range [{start_label}, {end_label}]")
            # Reset to start (will cause collisions but prevents crash)
            self.depth_counters[depth] = start_label
            label = start_label
        
        # Increment counter for next allocation
        self.depth_counters[depth] += 1
        
        return label
    
    def get_label_allocation_stats(self):
        """Get statistics about label allocation for debugging/monitoring.
        
        Returns:
            dict: Statistics including fixed depth ranges and current usage
        """
        stats = {
            'max_depth': self.max_depth,
            'labels_per_depth': self.labels_per_depth,
            'depths': {}
        }
        
        for depth in range(1, self.max_depth + 1):
            depth_range = self._get_depth_range(depth)
            if depth_range:
                start, end = depth_range
                current_counter = self.depth_counters.get(depth, start)
                used_in_range = current_counter - start
                available = end - current_counter + 1
                count = sum(1 for n in self.label_map.values() if n.depth == depth)
                
                stats['depths'][depth] = {
                    'fixed_range': (start, end),
                    'next_label': current_counter,
                    'allocated': used_in_range,
                    'available': available,
                    'active_objects': count
                }
        
        return stats
    
    def _get_window_for_label(self, label):
        """Find which window a label belongs to."""
        if label not in self.label_map:
            return None
        
        node = self.label_map[label]
        # Walk up to root to find window
        current = node
        while current.depth > 0:
            if current.depth == 1:
                # Parent is window root
                return current.obj_info.get('hwnd')
            # Try to find parent
            for hwnd, tree in self.window_trees.items():
                parent = self._find_parent_of_node(tree, current)
                if parent:
                    current = parent
                    break
            else:
                break
        
        # Check if node itself contains hwnd
        return node.obj_info.get('hwnd')
    
    def remove_label(self, label):
        """Remove a label and its subtree from the appropriate window tree."""
        if label not in self.label_map:
            return False
        
        node = self.label_map[label]
        
        # Find which window this label belongs to
        hwnd = None
        for window_hwnd, tree in self.window_trees.items():
            if self._node_in_tree(tree, node):
                hwnd = window_hwnd
                break
        
        if hwnd is None:
            return False
        
        # Clear pixels for this node and all children
        self._clear_node_pixels(node)
        
        # Remove node from parent's children list
        parent_node = self._find_parent_of_node(self.window_trees[hwnd], node)
        if parent_node:
            parent_node.children.remove(node)
        
        # Update subtree sizes for this window's tree
        self.window_trees[hwnd].update_subtree_size()
        
        return True
    
    def _node_in_tree(self, tree, target_node):
        """Check if a node exists in a tree."""
        if tree == target_node:
            return True
        for child in tree.children:
            if self._node_in_tree(child, target_node):
                return True
        return False
    
    def _clear_node_pixels(self, node):
        """Recursively clear pixels for a node and its children."""
        # Clear this node's pixels
        if node.label in self.label_map:
            self.image[self.image == node.label] = 0
            del self.label_map[node.label]
        
        # Clear children's pixels
        for child in node.children:
            self._clear_node_pixels(child)
    
    def _find_parent_of_node(self, current, target_node):
        """Find the parent of a specific node."""
        if current is None:
            return None
        
        for child in current.children:
            if child == target_node:
                return current
            parent = self._find_parent_of_node(child, target_node)
            if parent:
                return parent
        
        return None
    
    def fill_object_region(self, label, location, region):
        """Fill object region with label, only overwriting pixels with lower depth (lower label values).
        
        Args:
            label: The object label to write
            location: Object location (screen coordinates)
            region: Current capture region
        """
        # Convert to capture-layer-relative coordinates
        obj = Rect(location.left - region.left, location.top - region.top, location.width, location.height)
        
        # Clamp to image bounds
        img_height, img_width = self.image.shape[:2]
        image_rect = Rect(0, 0, img_width, img_height)
        obj_clamped = obj.intersection(image_rect)
                
        # Fill object region with label (only on pixels with lower depth/label)
        if obj_clamped.width > 0 and obj_clamped.height > 0:
            x1, y1, x2, y2 = obj_clamped.left, obj_clamped.top, obj_clamped.right, obj_clamped.bottom
            region_slice = self.image[y1:y2, x1:x2]
            # Only write where current label is lower (shallower depth) than new label
            self.image[y1:y2, x1:x2] = np.where(region_slice < label, label, region_slice)
        