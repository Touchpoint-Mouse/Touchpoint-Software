import math
import threading
import controlTypes
from .dependencies import np, cv2
from .utils import Rect, logMessage, is_window_occluded, get_actual_border_mask, get_object_window_handle, get_window_z_order
import time
import traceback
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
        """Update the layer image by cropping from the large captured buffer to the desired region.
        
        Handles cases where desired region extends beyond captured area by creating a blank
        image and copying only the available pixels.
        """
        with self.capture_lock:
            if self.capture_image is None or self.capture_region is None:
                return
            
            large_capture = self.capture_image
            capture_region = self.capture_region
        
        # Get the desired region (set by update_region_bounds)
        desired_region = self.layer.current_region
        
        # Find intersection between desired region and captured region
        intersection = desired_region.intersection(capture_region)
        
        # Create blank image with desired dimensions
        result_image = np.zeros((desired_region.height, desired_region.width, 3), dtype=np.uint8)
        
        # If there's an intersection, copy the overlapping pixels
        if intersection:
            # Transform intersection to local coordinates for cropping and pasting
            crop_rect = intersection.global_to_local(capture_region.top_left())
            paste_rect = intersection.global_to_local(desired_region.top_left())
            
            # Crop from captured image and paste into result
            cropped_pixels = large_capture[crop_rect.top:crop_rect.bottom, crop_rect.left:crop_rect.right]
            result_image[paste_rect.top:paste_rect.bottom, paste_rect.left:paste_rect.right] = cropped_pixels
        
        self.layer.update_image(result_image)
    
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
                
                # Create padded region using Rect
                padded_region = region.pad(padding)
                
                # Get screen bounds as Rect
                screen_width, screen_height = self.plugin.get_screen_size()
                screen_rect = Rect(0, 0, screen_width, screen_height)
                
                # Clamp padded region to screen bounds using intersection
                capture_rect = padded_region.intersection(screen_rect)
                if not capture_rect:
                    # No valid capture area, skip this iteration
                    continue
                
                # Create monitor dict for mss
                monitor = {
                    "left": capture_rect.left,
                    "top": capture_rect.top,
                    "width": capture_rect.width,
                    "height": capture_rect.height
                }
                
                screenshot = camera.grab(monitor)
                if screenshot is None:
                    return
                
                # Convert mss screenshot to numpy array
                frame = np.array(screenshot)
                
                # mss returns BGRA, extract BGR channels
                captured_image = frame[:, :, :3]
                
                # Store both image and the region it corresponds to atomically
                with self.capture_lock:
                    self.capture_image = captured_image
                    self.capture_region = capture_rect
            except Exception as e:
                logMessage(f"[ERROR] CaptureRenderer failed: {e}")
            
        time.sleep(self.plugin.config.software['threading']['capture'])  # Small delay to prevent excessive CPU usage
        
class ObjectRenderer(Renderer):
    """Renderer that finds unique NVDA objects using a mesh grid and updates the semantic map"""
    def __init__(self, capture_layer, object_layer):
        """Initialize with layer reference.
        
        Args:
            capture_layer: CaptureLayer to read from
            object_layer: ObjectLayer to write object labels to
        """
        self.capture_layer = capture_layer
        self.object_layer = object_layer
    
    def _invalidate_and_collect_existing_objects(self, region, prev_region, overlap_region, diff_mask):
        """Invalidate stale objects and collect remaining valid ones.
        
        Args:
            region: Current capture region
            prev_region: Previous capture region
            overlap_region: Intersection of current and previous regions
            diff_mask: Boolean mask of changed pixels
            
        Returns:
            tuple: (objects_needing_redraw, objects_fully_in_overlap) as lists of (label, location) tuples
        """
        objects_needing_redraw = []
        objects_fully_in_overlap = []
        labels_removed = 0
        
        for label, label_data in list(self.object_layer.label_map.items()):
            location = label_data['location']
            hwnd = label_data['hwnd']
            
            if location is None:
                continue
            
            # Check if this object is completely occluded by windows in front
            if is_window_occluded(hwnd, location):
                self.object_layer.remove_label(label)
                labels_removed += 1
                #logMessage(f"[ObjectRenderer] Removed occluded label {label} from window {hwnd}")
                continue
            
            # Check if object is within current capture region
            if not location.intersects(region):
                # Object completely outside current region - remove it
                self.object_layer.remove_label(label)
                labels_removed += 1
                #logMessage(f"[ObjectRenderer] Removed out-of-bounds label {label}")
                continue
            
            # Only check for invalidation if object intersects the overlap region
            # Objects entirely in new regions (no overlap) should not be invalidated
            if overlap_region and location.intersects(overlap_region):
                # Convert object's FULL location to current image local coordinates
                obj_rect_local = location.global_to_local(region.top_left())
                
                # Get border mask for the FULL object using actual borders (not clamped)
                # This avoids creating artificial borders when object extends beyond capture region
                object_border_mask = get_actual_border_mask(obj_rect_local, diff_mask.shape)
                
                # Convert overlap region to local coordinates to create a mask
                overlap_local = overlap_region.global_to_local(region.top_left())
                
                # Create mask for overlap region
                overlap_mask = np.zeros(diff_mask.shape, dtype=bool)
                overlap_clamped = overlap_local.intersection(Rect(0, 0, diff_mask.shape[1], diff_mask.shape[0]))
                if overlap_clamped and overlap_clamped.width > 0 and overlap_clamped.height > 0:
                    overlap_mask[overlap_clamped.top:overlap_clamped.bottom, 
                                overlap_clamped.left:overlap_clamped.right] = True
                
                # Only check border pixels that are within the overlap region
                border_in_overlap = object_border_mask & overlap_mask
                border_changed = np.any(diff_mask[border_in_overlap])
                
                if border_changed:
                    # Before invalidating, try to reacquire object from its mesh point
                    mesh_point = label_data.get('mesh_point')
                    should_invalidate = True
                    
                    if mesh_point is not None:
                        try:
                            mesh_x, mesh_y = mesh_point
                            # Reacquire object from the same mesh point
                            reacquired_obj = NVDAObjects.NVDAObject.objectFromPoint(int(mesh_x), int(mesh_y))
                            
                            if reacquired_obj and hasattr(reacquired_obj, 'location') and reacquired_obj.location:
                                # Create obj_info for reacquired object
                                reacquired_obj_info = {
                                    'name': reacquired_obj.name if hasattr(reacquired_obj, 'name') else 'Unknown',
                                    'role': reacquired_obj.role if hasattr(reacquired_obj, 'role') else None,
                                    'location': reacquired_obj.location,
                                    'object': reacquired_obj
                                }
                                
                                # Use existing duplicate checking logic
                                reacquired_obj_nvda, reacquired_hwnd, reacquired_location = \
                                    self.object_layer._validate_and_extract_object(reacquired_obj_info)
                                
                                if reacquired_obj_nvda and reacquired_hwnd and reacquired_location:
                                    # Check if reacquired object is a duplicate of the existing label
                                    duplicate_label = self.object_layer._check_duplicate_object(
                                        reacquired_obj_info, reacquired_hwnd, reacquired_location
                                    )
                                    
                                    if duplicate_label == label:
                                        # Same object still at the same location - don't invalidate
                                        should_invalidate = False
                                        debug_msg = f"Reacquired label {label} from mesh point {mesh_point} - border changed but object unchanged"
                                        self.plugin.hardware.send_debug_log(debug_msg)
                        except Exception as e:
                            # If reacquisition fails, proceed with invalidation
                            pass
                    
                    if should_invalidate:
                        # Border pixels changed - invalidate this object
                        self.object_layer.remove_label(label)
                        labels_removed += 1
                        #logMessage(f"[ObjectRenderer] Removed invalidated label {label} (border changed)")
                        # Log object name, role, and value to debug log
                        obj_info = label_data.get('obj_info', {})
                        obj = obj_info.get('object')
                        obj_value = getattr(obj, 'value', 'N/A') if obj else 'N/A'
                        debug_msg = f"Invalidated label {label} (name: {obj_info.get('name', 'Unknown')}, role: {obj_info.get('role', 'Unknown')}, value: {obj_value}), location: {location}"
                        self.plugin.hardware.send_debug_log(debug_msg)
                        continue
            
            # Object not invalidated - determine if it needs redrawing
            # Objects fully inside overlap region can keep their pixels
            # Objects extending outside overlap need redrawing for new visible areas
            if overlap_region and overlap_region.contains(location):
                # Object fully inside overlap region - pixels are still valid
                objects_fully_in_overlap.append((label, location))
            else:
                # Object extends outside overlap or overlap is None - needs redrawing
                objects_needing_redraw.append((label, location))
        
        return objects_needing_redraw, objects_fully_in_overlap
    
    def _detect_object_at_point(self, x, y, region, mesh_width, mesh_height, current_object_image):
        """Detect and validate NVDA object at a specific screen point.
        
        Args:
            x, y: Screen coordinates to check
            region: Current capture region
            mesh_width, mesh_height: Mesh grid dimensions
            current_object_image: Current object layer image
            
        Returns:
            tuple: (obj_info dict, label) if valid object detected, (None, 0) otherwise
        """
        # Continue if this point already has a label in the working image (skip to next point)
        y_rel = y - region.top
        x_rel = x - region.left
        
        pixel_value = current_object_image[y_rel, x_rel]
        if pixel_value != 0:
            return None, 0
        
        try:
            # Get NVDA object at this screen position
            obj = NVDAObjects.NVDAObject.objectFromPoint(int(x), int(y))
            
            # Skip if no object or object has no location (can't label it)
            if obj is None:
                return None, 0
            
            if not hasattr(obj, 'location') or obj.location is None:
                return None, 0
            
            # Skip if any of the object's dimensions are smaller than the mesh grid cell size
            cell_width = region.width / mesh_width
            cell_height = region.height / mesh_height
            
            try:
                obj_width = obj.location.width
                obj_height = obj.location.height
            except (AttributeError, TypeError):
                # Location object doesn't have width/height attributes
                return None, 0
            
            if obj_width < cell_width or obj_height < cell_height:
                return None, 0
            
            # Create obj_info dictionary (must include 'object' for internal processing)
            obj_info = {
                'name': obj.name if hasattr(obj, 'name') else 'Unknown',
                'role': obj.role if hasattr(obj, 'role') else None,
                'location': obj.location,
                'object': obj
            }
            
            # Add to object layer (handles parent finding and depth calculation internally)
            # Pass mesh point where this object was detected
            new_label = self.object_layer.add_label(obj_info, mesh_point=(x, y))
            
            return obj_info, new_label
            
        except Exception as e:
            return None, 0
    
    def _perform_mesh_grid_scan(self, region, mesh_width, mesh_height, current_object_image):
        """Perform mesh grid scan to detect new objects.
        
        Args:
            region: Current capture region
            mesh_width, mesh_height: Mesh grid dimensions
            current_object_image: Current object layer image
            
        Returns:
            list: List of (label, location) tuples for newly detected objects
        """
        # Generate evenly spaced points across the capture region including edges
        # linspace includes both endpoints, so edges are covered
        x_points = np.linspace(region.left, region.left + region.width - 1, mesh_width, dtype=int)
        y_points = np.linspace(region.top, region.top + region.height - 1, mesh_height, dtype=int)
        
        # Collect new detections during mesh scan
        new_detections = []
        
        # Gets screen bounds for occlusion checking during mesh scan
        screen_width, screen_height = self.plugin.get_screen_size()
        
        # Loop through mesh grid and detect unique objects
        for y in y_points:
            for x in x_points:
                # Checks to ensure global coordinates are within screen bounds
                if y < 0 or y >= screen_height or x < 0 or x >= screen_width:
                    continue
                
                obj_info, new_label = self._detect_object_at_point(
                    x, y, region, mesh_width, mesh_height, current_object_image
                )
                
                if new_label > 0:
                    # Use location from label_map (converted to Rect) instead of raw NVDA location
                    stored_location = self.object_layer.label_map.get(new_label, {}).get('location')
                    new_detections.append((new_label, stored_location))
        
        return new_detections
        
    def __call__(self):
        """Find unique NVDA objects using a mesh grid and update the semantic map"""
        try:
            # Get capture layer difference mask to determine which regions have changed
            diff_mask = self.capture_layer.get_diff_mask()
            
            if diff_mask.size == 0 or not np.any(diff_mask):
                return  # No changes, skip processing
            
            # Get current and previous capture regions
            region = self.capture_layer.current_region
            prev_region = self.capture_layer.prev_region
            
            # Calculate the overlap region between previous and current capture regions
            overlap_region = prev_region.intersection(region)
            
            # Get object mesh dimensions from config
            if not self.plugin or not hasattr(self.plugin, 'config'):
                logMessage("[ObjectRenderer] No plugin or config available")
                return
                
            mesh_dims = self.plugin.config.layer_dimensions.get('object_mesh')
            if mesh_dims is None:
                logMessage("[ObjectRenderer] No object_mesh dimensions in config")
                return
                
            mesh_width, mesh_height = mesh_dims
            
            # Invalidate stale objects and separate into those needing redraw vs those fully in overlap
            objects_needing_redraw, objects_fully_in_overlap = self._invalidate_and_collect_existing_objects(
                region, prev_region, overlap_region, diff_mask
            )
            
            # Get current object image for mesh grid detection
            # Objects fully in overlap have valid pixels, so we don't need to clear everything
            current_object_image = self.object_layer.get_image()
            
            # Perform mesh grid scan to detect new objects
            new_detections = self._perform_mesh_grid_scan(
                region, mesh_width, mesh_height, current_object_image
            )
            
            # Bulk redraw: objects needing redraw + new detections
            # (objects fully in overlap don't need redraw, their pixels are still valid)
            all_objects_to_draw = objects_needing_redraw + new_detections
            for label, location in all_objects_to_draw:
                self.object_layer.fill_object_region(label, location, region)
                        
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
    """Renderer that reads from object layer and writes scaled depth to depth layer."""
    
    def __init__(self, object_layer, depth_layer):
        """Initialize with references to object and depth layers.
        
        Args:
            object_layer: ObjectLayer to read object labels from
            depth_layer: DepthLayer to write depth values to
        """
        self.object_layer = object_layer
        self.depth_layer = depth_layer
        
    def __call__(self):
        """Convert object labels to scaled depth values and write to depth layer."""
        try:
            # Get object layer image
            object_img = self.object_layer.get_image()
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
        
        # Get compare dimensions (minimum of old and new image dimensions adjusted for offset)
        compare_width = min(self.prev_image.shape[1] - abs(dx), img_width)
        compare_height = min(self.prev_image.shape[0] - abs(dy), img_height)
        
        if compare_width > 0 and compare_height > 0:
            # Calculate overlapping region to compare (pixels shift opposite to region movement)
            # Source region in previous image (what to compare from)
            src_rect = Rect(max(0, dx), max(0, dy), width=compare_width, height=compare_height)
            
            # Destination region in current image (what to compare to)
            dst_rect = Rect(max(0, -dx), max(0, -dy), width=compare_width, height=compare_height)
            
            # Get overlapping regions from both images
            prev_overlap = self.prev_image[src_rect.top:src_rect.bottom, src_rect.left:src_rect.right]
            curr_overlap = self.image[dst_rect.top:dst_rect.bottom, dst_rect.left:dst_rect.right]
            
            # Compare pixels - check if any channel differs
            if len(self.image.shape) > 2:  # Multi-channel image
                pixel_diff = np.any(prev_overlap != curr_overlap, axis=2)
            else:  # Single-channel image
                pixel_diff = prev_overlap != curr_overlap
            
            # Update diff mask for overlapping region
            diff_mask[dst_rect.top:dst_rect.bottom, dst_rect.left:dst_rect.right] = pixel_diff
        
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
        old_region = self.current_region.copy()
        
        # Update current region
        self.current_region = new_region.copy()
        
        # Determine target image size
        if self.constant_size:
            # Use constant size (width, height tuple)
            target_width, target_height = self.constant_size
        else:
            # Use new region size
            target_width = new_region.width
            target_height = new_region.height
        
        # Initialize new image and difference mask
        dtype = old_image.dtype if old_image is not None else np.uint8
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
            
            # Get copy width (minimum of old and new image dimensions adjusted for offset)
            copy_width = min(old_image.shape[1] - abs(dx), target_width)
            copy_height = min(old_image.shape[0] - abs(dy), target_height)
            
            # When region moves right, layer content shifts left (opposite direction)
            # Source region in old image (what to copy from)
            src_rect = Rect(max(0, dx), max(0, dy), width=copy_width, height=copy_height)
            
            # Destination region in new image (where to copy to)
            dst_rect = Rect(max(0, -dx), max(0, -dy), width=copy_width, height=copy_height)
            
            if copy_width > 0 and copy_height > 0:
                # Copy overlapping region
                new_image[dst_rect.top:dst_rect.bottom, 
                            dst_rect.left:dst_rect.right] = \
                    old_image[src_rect.top:src_rect.bottom,
                                src_rect.left:src_rect.right]
        
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
    
class ObjectLayer(RenderLayer):
    """Render layer that stores object label information with depth-based allocation."""
    
    def __init__(self, id, constant_size=None, max_depth=5, labels_per_depth=50):
        super().__init__(id, dtype=np.uint16, constant_size=constant_size, num_channels=1)
        # Flat dict mapping label -> object info with depth tracking
        self.label_map = {}  # label -> {'obj_info': dict, 'location': Rect, 'depth': int, 'hwnd': int}
        
        # Window tracking for z-order and root management
        self.window_z_orders = {}  # hwnd -> z-order position (0 = topmost)
        self.window_labels = {}  # hwnd -> window label (depth 0)
        
        # Configurable depth-based label allocation
        self.max_depth = max_depth  # Maximum supported depth levels (configurable)
        self.labels_per_depth = labels_per_depth  # Fixed allocation per depth level (configurable)
        self.depth_counters = {}  # depth_level -> next available label in that depth's range
        
    def _get_or_create_window_label(self, hwnd, window_obj=None):
        """Get or create label for window at depth level 0.
        
        Args:
            hwnd: Window handle
            window_obj: Optional NVDA window object to store
        
        Returns:
            int: Window label (depth 0)
        """
        from .utils import get_window_z_order, get_window_rect
        
        # Check if window already has a label
        if hwnd in self.window_labels:
            return self.window_labels[hwnd]
        
        # Track window z-order
        if hwnd not in self.window_z_orders:
            window_z_order = get_window_z_order(hwnd)
            self.window_z_orders[hwnd] = window_z_order if window_z_order >= 0 else 999
        
        # Allocate label for window at depth level 0
        window_label = self._calculate_label_for_depth(0)
        if window_label == 0:
            logMessage(f"[ObjectLayer] Failed to allocate label for window {hwnd}")
            return 0
        
        # Create window object info
        window_info = {
            'name': f'Window {hwnd}',
            'role': None,  # Will be set if window_obj provided
            'hwnd': hwnd
        }
        
        if window_obj:
            window_info['name'] = window_obj.name if hasattr(window_obj, 'name') else f'Window {hwnd}'
            window_info['role'] = window_obj.role if hasattr(window_obj, 'role') else None
            window_info['object'] = window_obj
        
        # Get window location
        window_rect = get_window_rect(hwnd)
        if window_rect:
            window_location = Rect(window_rect.left, window_rect.top, 
                                  width=window_rect.width, height=window_rect.height)
        else:
            window_location = None
        
        # Store window in label map at depth 0
        self.label_map[window_label] = {
            'obj_info': window_info,
            'location': window_location,
            'depth': 0,
            'hwnd': hwnd
        }
        
        self.window_labels[hwnd] = window_label
        #logMessage(f"[ObjectLayer] Created window label {window_label} for hwnd {hwnd} at depth 0")
        
        return window_label
    
    def _validate_and_extract_object(self, obj_info):
        """Validate object info and extract window handle and location.
        
        Args:
            obj_info: Dictionary containing object information
            
        Returns:
            tuple: (obj, obj_hwnd, location_rect) or (None, None, None) if invalid
        """
        from .utils import get_object_window_handle
        
        # Get window handle for this object
        obj = obj_info.get('object')
        if not obj:
            logMessage("[ObjectLayer] add_label: No object in obj_info")
            return None, None, None
        
        obj_hwnd = get_object_window_handle(obj)
        if not obj_hwnd:
            logMessage("[ObjectLayer] add_label: No window handle for object")
            return None, None, None
        
        # Extract and convert location
        obj_location = obj_info.get('location')
        if obj_location:
            try:
                location_rect = Rect(obj_location.left, obj_location.top, 
                                   width=obj_location.width, height=obj_location.height)
            except (AttributeError, TypeError):
                # location object doesn't have expected attributes
                location_rect = None
        else:
            location_rect = None
        
        return obj, obj_hwnd, location_rect
    
    def _check_duplicate_object(self, obj_info, obj_hwnd, location_rect):
        """Check if object already exists in label map.
        
        Args:
            obj_info: Object information dictionary
            obj_hwnd: Window handle
            location_rect: Object location as Rect
            
        Returns:
            int: Existing label if duplicate found, 0 otherwise
        """
        if location_rect is None:
            return 0
        
        for existing_label, existing_data in self.label_map.items():
            if existing_data['hwnd'] != obj_hwnd:
                continue  # Different window
            existing_obj_info = existing_data['obj_info']
            if (existing_obj_info.get('name') == obj_info.get('name') and
                existing_obj_info.get('role') == obj_info.get('role') and
                existing_data['location'] == location_rect):
                return existing_label  # Already exists
        
        return 0
    
    def _find_parent_in_label_map(self, obj, obj_hwnd, location_rect):
        """Find parent object in existing label map.
        
        Tries NVDA parent property first, then falls back to spatial containment.
        
        Args:
            obj: NVDA object
            obj_hwnd: Window handle
            location_rect: Object location as Rect
            
        Returns:
            int: Parent label if found, None otherwise
        """
        found_parent_label = None
        
        # First, try to use NVDA's parent property
        if hasattr(obj, 'parent') and obj.parent:
            parent_obj = obj.parent
            # Check if parent exists in our label map by matching name, role, location
            if hasattr(parent_obj, 'location') and parent_obj.location:
                try:
                    parent_location = Rect(parent_obj.location.left, parent_obj.location.top,
                                          width=parent_obj.location.width, height=parent_obj.location.height)
                except (AttributeError, TypeError):
                    # Parent location doesn't have expected attributes
                    parent_location = None
                
                if parent_location:
                    parent_name = parent_obj.name if hasattr(parent_obj, 'name') else None
                    parent_role = parent_obj.role if hasattr(parent_obj, 'role') else None
                    
                    # Find matching parent in label map (must be same window)
                    for candidate_label, candidate_data in self.label_map.items():
                        if candidate_data['hwnd'] != obj_hwnd:
                            continue  # Different window
                        candidate_obj_info = candidate_data['obj_info']
                        if (candidate_obj_info.get('name') == parent_name and
                            candidate_obj_info.get('role') == parent_role and
                            candidate_data['location'] == parent_location):
                            found_parent_label = candidate_label
                            break
        
        # Fallback: find smallest existing object that contains this object (same window)
        if found_parent_label is None and location_rect:
            smallest_containing_area = float('inf')
            
            for candidate_label, candidate_data in self.label_map.items():
                if candidate_data['hwnd'] != obj_hwnd:
                    continue  # Different window
                candidate_location = candidate_data['location']
                if candidate_location and candidate_location.contains(location_rect):
                    container_area = candidate_location.area()
                    if container_area < smallest_containing_area:
                        smallest_containing_area = container_area
                        found_parent_label = candidate_label
        
        return found_parent_label
    
    def _calculate_depth_from_tree(self, obj, obj_hwnd):
        """Calculate depth level by traversing NVDA tree to window.
        
        Args:
            obj: NVDA object
            obj_hwnd: Window handle
            
        Returns:
            tuple: (depth_level, window_obj) - depth from window and window object if found
        """
        current_obj = obj
        tree_depth_count = 0
        window_obj = None
        
        # Count steps from object to window through NVDA parent chain
        while hasattr(current_obj, 'parent') and current_obj.parent:
            tree_depth_count += 1
            current_obj = current_obj.parent
            
            # Check if we've reached a window (has windowHandle and windowHandle matches itself)
            if hasattr(current_obj, 'windowHandle') and current_obj.windowHandle:
                # Verify this is actually the window object (not just a child with windowHandle)
                current_hwnd = current_obj.windowHandle
                if current_hwnd == obj_hwnd:
                    # Found the window object
                    window_obj = current_obj
                    break
            
            # Prevent infinite loops
            if tree_depth_count > 20:
                logMessage("[ObjectLayer] add_label: NVDA tree traversal exceeded 20 levels, breaking")
                break
        
        # Verify we reached a valid window object
        if window_obj is None:
            # Didn't find window in tree - this might be the window itself or orphaned object
            # Check if current object IS the window
            if hasattr(obj, 'windowHandle') and obj.windowHandle == obj_hwnd:
                # This object is a window - check if no parent or parent is desktop
                if not hasattr(obj, 'parent') or obj.parent is None:
                    window_obj = obj
                    tree_depth_count = 0  # Window itself is at depth 0
        
        return tree_depth_count, window_obj
    
    def add_label(self, obj_info, mesh_point=None):
        """Add a new object label with depth-based allocation.
        
        Automatically finds parent in existing labels and calculates depth level.
        If no parent found in labels, traverses NVDA tree to determine depth level.
        Windows are automatically created at depth 0 when first child is detected.
        
        Args:
            obj_info: Dictionary containing object information (name, role, location, 'object')
            mesh_point: Optional tuple (x, y) of screen coordinates where object was detected
        Returns:
            int: The label assigned to this object
        """
        # Validate object and extract window handle and location
        obj, obj_hwnd, location_rect = self._validate_and_extract_object(obj_info)
        if obj is None:
            return 0
        
        # Check for duplicates
        existing_label = self._check_duplicate_object(obj_info, obj_hwnd, location_rect)
        if existing_label > 0:
            return existing_label
        
        # Find parent label in existing labels
        found_parent_label = self._find_parent_in_label_map(obj, obj_hwnd, location_rect)
        
        # Calculate depth level based on parent or NVDA tree traversal
        if found_parent_label is not None:
            # Parent exists in our label map - depth level is one level deeper
            parent_depth_level = self.label_map[found_parent_label]['depth']
            object_depth_level = parent_depth_level + 1
        else:
            # No parent in label map - traverse NVDA tree to calculate depth level from window
            tree_depth_count, window_obj = self._calculate_depth_from_tree(obj, obj_hwnd)
            
            # Ensure window label exists at depth 0
            if tree_depth_count > 0 or window_obj is not None:
                # Create window label if it doesn't exist
                window_label = self._get_or_create_window_label(obj_hwnd, window_obj)
                if window_label == 0:
                    logMessage(f"[ObjectLayer] add_label: Failed to create window label for hwnd {obj_hwnd}")
                    return 0
            
            # Depth level is distance from window (depth 0 = window, depth 1 = direct children, etc.)
            object_depth_level = tree_depth_count
        
        # Allocate label from the depth level's range
        allocated_label = self._calculate_label_for_depth(object_depth_level)
        
        # Verify label is valid
        if allocated_label == 0:
            logMessage(f"[ObjectLayer] add_label: Failed to allocate label for depth level {object_depth_level}")
            return 0
        
        # Store object data with allocated label
        self.label_map[allocated_label] = {
            'obj_info': obj_info,
            'location': location_rect,
            'depth': object_depth_level,
            'hwnd': obj_hwnd,
            'mesh_point': mesh_point  # Store mesh point where object was detected
        }
        
        return allocated_label
    
    def _get_depth_range(self, depth_level):
        """Get the fixed (start_label, end_label) range for a depth level.
        
        Args:
            depth_level: Depth level (0 = windows, 1 = direct children, 2+ = descendants)
        
        Returns:
            Tuple of (start_label, end_label) or None if depth level exceeds max
        """
        if depth_level > self.max_depth:
            return None  # Depth level exceeds maximum
        
        # Label ranges per depth level (configurable via software_config.json):
        # With default labels_per_depth=50:
        #   Depth level 0 (windows): labels 1-50
        #   Depth level 1: labels 51-100
        #   Depth level 2: labels 101-150
        #   Depth level 3: labels 151-200
        #   Depth level 4: labels 201-250
        #   Depth level 5: labels 251-300
        start_label = depth_level * self.labels_per_depth + 1
        end_label = (depth_level + 1) * self.labels_per_depth
        
        return (start_label, end_label)
    
    def _calculate_label_for_depth(self, depth_level):
        """Allocate label from fixed depth level range.
        
        Each depth level has a configurable range of labels (set in software_config.json).
        With default settings (labels_per_depth=50):
        - Depth level 0 (windows): labels 1-50
        - Depth level 1 (direct children): labels 51-100
        - Depth level 2: labels 101-150
        - Depth level 3: labels 151-200
        - Depth level 4: labels 201-250
        - Depth level 5: labels 251-300
        
        Args:
            depth_level: The depth level for which to allocate a label (0 to max_depth)
        
        Returns:
            int: Label for the new object, or 0 if allocation failed
        """
        # Get fixed range for this depth level
        depth_range = self._get_depth_range(depth_level)
        if depth_range is None:
            logMessage(f"[ERROR] Depth level {depth_level} exceeds max_depth {self.max_depth}")
            return 0
        
        start_label, end_label = depth_range
        
        # Initialize counter for this depth level if needed
        if depth_level not in self.depth_counters:
            self.depth_counters[depth_level] = start_label
        
        # Get next available label from this depth level's range
        label = self.depth_counters[depth_level]
        
        # Check if we've exhausted this depth level's range
        if label > end_label:
            logMessage(f"[ERROR] Depth level {depth_level} exhausted its label range [{start_label}, {end_label}]")
            # Reset to start (will cause collisions but prevents crash)
            self.depth_counters[depth_level] = start_label
            label = start_label
        
        # Increment counter for next allocation
        self.depth_counters[depth_level] += 1
        
        return label
    
    def get_label_allocation_stats(self):
        """Get statistics about label allocation for debugging/monitoring.
        
        Returns:
            dict: Statistics including fixed depth level ranges and current usage
        """
        stats = {
            'max_depth': self.max_depth,
            'labels_per_depth': self.labels_per_depth,
            'depth_levels': {}
        }
        
        for depth_level in range(0, self.max_depth + 1):
            depth_range = self._get_depth_range(depth_level)
            if depth_range:
                start, end = depth_range
                current_counter = self.depth_counters.get(depth_level, start)
                used_in_range = current_counter - start
                available = end - current_counter + 1
                count = sum(1 for data in self.label_map.values() if data['depth'] == depth_level)
                
                stats['depth_levels'][depth_level] = {
                    'fixed_range': (start, end),
                    'next_label': current_counter,
                    'allocated': used_in_range,
                    'available': available,
                    'active_objects': count
                }
        
        return stats
    
    def remove_label(self, label):
        """Remove a label and recycle its position in the depth range."""
        if label not in self.label_map:
            return False
        
        # Get depth level and hwnd before removing
        label_depth = self.label_map[label]['depth']
        label_hwnd = self.label_map[label]['hwnd']
        
        # Clear pixels for this node
        self.image[self.image == label] = 0
        
        # Remove from label map
        del self.label_map[label]
        
        # If this was a window label (depth 0), remove from window tracking
        if label_depth == 0 and label_hwnd in self.window_labels:
            if self.window_labels[label_hwnd] == label:
                del self.window_labels[label_hwnd]
                #logMessage(f"[ObjectLayer] Removed window label {label} for hwnd {label_hwnd}")
        
        # Recycle the label position by resetting depth counter if this label is earlier
        if label_depth in self.depth_counters:
            # If this freed label is less than the current counter, reset to reuse it
            if label < self.depth_counters[label_depth]:
                self.depth_counters[label_depth] = label
                #logMessage(f"[ObjectLayer] Recycled label {label} at depth {label_depth}, reset counter")
        
        return True
    
    def fill_object_region(self, label, location, region):
        """Fill object region with label, respecting depth ordering.
        
        Args:
            label: The object label to write
            location: Object location (Rect, screen coordinates)
            region: Current capture region (Rect)
        """
        # Skip if location is None
        if location is None:
            return
        
        # Convert to capture-layer-relative coordinates
        obj = Rect(location.left - region.left, location.top - region.top, 
                   right=location.right - region.left, bottom=location.bottom - region.top)
        
        # Clamp to image bounds
        img_height, img_width = self.image.shape[:2]
        image_rect = Rect(0, 0, img_width, img_height)
        obj_clamped = obj.intersection(image_rect)
                
        # Fill object region with label - only overwrite lower labels (shallower depth)
        # This ensures children (higher labels) are not overwritten by parents
        if obj_clamped and obj_clamped.width > 0 and obj_clamped.height > 0:
            x1, y1, x2, y2 = obj_clamped.left, obj_clamped.top, obj_clamped.right, obj_clamped.bottom
            region_slice = self.image[y1:y2, x1:x2]
            # Only write where current label is lower (shallower depth) than new label
            self.image[y1:y2, x1:x2] = np.where(region_slice < label, label, region_slice)
        