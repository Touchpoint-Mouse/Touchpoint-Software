import math
import threading
import controlTypes
from .dependencies import np, cv2
from .utils import Rect, logMessage, get_actual_border_mask, get_object_window_handle, get_window_z_order
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
        super().__init__()
        self.capture_layer = capture_layer
        self.object_layer = object_layer
    
    def _check_occlusion(self, hwnd, obj_location, capture_region):
        """Check if a window/object is completely occluded by windows in front using cached z-orders.
        
        Args:
            hwnd: Window handle to check
            obj_location: Object location Rect to check
            capture_region: Capture region Rect to constrain the check
            
        Returns:
            bool: True if completely occluded, False otherwise
        """
        try:
            from .utils import get_window_rect
            import winUser
            
            if not hwnd or obj_location is None:
                return False
            
            # Get the region to check within capture region
            check_rect = obj_location.copy()
            visible_rect = check_rect.intersection(capture_region)
            if not visible_rect:
                # No intersection with capture region - consider it occluded (not visible)
                return True
            
            # Get z-order from cached values
            target_z_order = self.object_layer.window_z_orders.get(hwnd, 999)
            if target_z_order < 0:
                return False
            
            # If z-order is still 999 (placeholder), update z-orders now
            if target_z_order == 999:
                from .utils import update_window_z_orders
                logMessage(f"[ObjectRenderer] Z-order not set for hwnd {hwnd}, updating all z-orders")
                update_window_z_orders(self.object_layer.window_z_orders)
                self.object_layer.update_z_order_lookups()
                target_z_order = self.object_layer.window_z_orders.get(hwnd, 999)
                if target_z_order == 999:
                    logMessage(f"[ObjectRenderer] Z-order still 999 after update for hwnd {hwnd}, skipping occlusion check")
                    return False
            
            # Check windows that are currently in the object tree and are in front (lower z-order)
            for other_hwnd in self.object_layer.window_labels.keys():
                if other_hwnd == hwnd:
                    continue
                
                # Get z-order for this window
                other_z_order = self.object_layer.window_z_orders.get(other_hwnd, 999)
                
                # Only check windows in front (desktop/shell windows have z-order 9998)
                # Skip windows with z-order >= 9998
                if other_z_order >= 9998:
                    continue
                    
                if other_z_order < target_z_order:
                    # Get the rect of the window in front
                    front_rect = get_window_rect(other_hwnd)
                    if not front_rect:
                        continue
                    
                    # Check if this window in front completely covers our region
                    if front_rect.contains(visible_rect):
                        # Check if it's visible (not minimized)
                        try:
                            if winUser.isWindowVisible(other_hwnd):
                                logMessage(f"[ObjectRenderer] Occlusion: hwnd {hwnd} (z={target_z_order}) covered by hwnd {other_hwnd} (z={other_z_order})")
                                return True
                        except:
                            pass
            
            return False
        except Exception as e:
            logMessage(f"Error checking window occlusion: {e}")
            return False
    
    def _invalidate_and_collect_existing_objects(self, region, overlap_region, diff_mask):
        """Invalidate stale objects and collect remaining valid ones.
        
        Args:
            region: Current capture region
            prev_region: Previous capture region
            overlap_region: Intersection of current and previous regions
            diff_mask: Boolean mask of changed pixels
            
        Returns:
            list: List of (label, location) tuples for objects needing redraw
        """
        objects_needing_redraw = []
        labels_removed = 0
        
        for label, label_data in list(self.object_layer.label_map.items()):
            location = label_data.location
            hwnd = label_data.hwnd
            
            if location is None:
                continue
            
            # Check if this object is completely occluded by windows in front within the capture region
            # (This also handles out of bounds - objects outside capture region are considered occluded)
            if self._check_occlusion(hwnd, location, region):
                self.object_layer.remove_label(label)
                # Logging moved to _check_occlusion for detailed info
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
                    mesh_point = label_data.mesh_point
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
                                    # Use fast role-based check during reacquisition (skip expensive window validation)
                                    duplicate_label = self.object_layer._check_duplicate_object(
                                        reacquired_obj_info, reacquired_hwnd, reacquired_location, 
                                        check_window_validation=False
                                    )
                                    
                                    if duplicate_label == label:
                                        # Same object still at the same location - don't invalidate
                                        should_invalidate = False
                                        obj_name = label_data.obj_info.get('name', 'Unknown')[:20] if label_data.obj_info else 'Unknown'
                                        logMessage(f"[ObjectRenderer] Reacquired label {label} '{obj_name}' - border changed but object unchanged")
                                    else:
                                        obj_name = label_data.obj_info.get('name', 'Unknown')[:20] if label_data.obj_info else 'Unknown'
                                        logMessage(f"[ObjectRenderer] Label {label} '{obj_name}' border changed - duplicate_label={duplicate_label}, will invalidate")
                        except Exception as e:
                            # If reacquisition fails, proceed with invalidation
                            obj_name = label_data.obj_info.get('name', 'Unknown')[:20] if label_data.obj_info else 'Unknown'
                            logMessage(f"[ObjectRenderer] Reacquisition failed for label {label} '{obj_name}': {e}")
                            pass
                    
                    if should_invalidate:
                        # Border pixels changed - invalidate this object
                        self.object_layer.remove_label(label)
                        obj_name = label_data.obj_info.get('name', 'Unknown')[:20] if label_data.obj_info else 'Unknown'
                        logMessage(f"[ObjectRenderer] Invalidated label {label} '{obj_name}' - border changed")
                        continue
            
            # Object not invalidated - add to redraw list
            # Redraw all non-invalidated objects regardless of overlap
            objects_needing_redraw.append((label, location))
        
        return objects_needing_redraw
    
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
                    # Use location from label_map (TreeNode) instead of raw NVDA location
                    node = self.object_layer.label_map.get(new_label)
                    stored_location = node.location if node else None
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
            objects_needing_redraw = self._invalidate_and_collect_existing_objects(
                region, overlap_region, diff_mask
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
        # Store dtype for consistent array creation
        self.dtype = dtype
        
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
        # Use the layer's stored dtype (set during __init__)
        dtype = self.dtype
        # Use stored num_channels from layer initialization
        num_channels = self.num_channels
        
        if num_channels > 1:
            new_image = np.zeros((target_height, target_width, num_channels), dtype=dtype)
        else:
            new_image = np.zeros((target_height, target_width), dtype=dtype)
        
        # If we have an old image, copy overlapping region
        if old_image is not None and old_image.size > 0:
            # Calculate relative offset (how much the region moved in screen coordinates)
            dx_screen = new_region.left - old_region.left
            dy_screen = new_region.top - old_region.top
            
            # For constant_size layers, scale the screen offset to layer coordinates
            # E.g., if depth layer is 20x20 representing a 400x400 screen region,
            # moving 10 screen pixels should only shift 0.5 layer pixels
            if self.constant_size:
                # Calculate scaling factors
                scale_x = target_width / old_region.width if old_region.width > 0 else 1.0
                scale_y = target_height / old_region.height if old_region.height > 0 else 1.0
                
                # Scale offsets to layer coordinates
                dx = int(round(dx_screen * scale_x))
                dy = int(round(dy_screen * scale_y))
            else:
                # Dynamic-size layers use screen offsets directly
                dx = dx_screen
                dy = dy_screen
            
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


class TreeNode:
    """Node in the object hierarchy tree.
    
    Each node represents an object and tracks its parent-child relationships.
    """
    def __init__(self, label, obj_info, location, depth, hwnd, mesh_point=None):
        self.label = label
        self.obj_info = obj_info
        self.location = location
        self.depth = depth
        self.hwnd = hwnd
        self.mesh_point = mesh_point
        self.parent = None  # Reference to parent TreeNode
        self.children = []  # List of child TreeNode references
    
    def add_child(self, child_node):
        """Add a child node and set its parent reference."""
        if child_node not in self.children:
            self.children.append(child_node)
            child_node.parent = self
    
    def remove_child(self, child_node):
        """Remove a child node and clear its parent reference."""
        if child_node in self.children:
            self.children.remove(child_node)
            child_node.parent = None
    
    def contains(self, other_node):
        """Check if this node's bounding box contains another node's box."""
        if not self.location or not other_node.location:
            return False
        return self.location.contains(other_node.location)
    
    def update_depth_recursively(self, new_depth, object_layer):
        """Recursively update depth for this node and all descendants.
        
        This requires reallocating labels when depth changes.
        
        Args:
            new_depth: The new depth to set for this node
            object_layer: ObjectLayer instance for reallocating labels
        """
        # Reallocate label if depth changed
        if self.depth != new_depth:
            object_layer._reallocate_node_label(self, new_depth)
        
        # Recursively update children
        for child in self.children:
            child.update_depth_recursively(new_depth + 1, object_layer)
    
    def get_all_descendants(self):
        """Get all descendant nodes (children, grandchildren, etc.).
        
        Returns:
            list: All TreeNode descendants
        """
        descendants = []
        for child in self.children:
            descendants.append(child)
            descendants.extend(child.get_all_descendants())
        return descendants
    
    def __repr__(self):
        """String representation for debugging."""
        name = self.obj_info.get('name', 'Unknown')[:20]
        return f"TreeNode(label={self.label}, depth={self.depth}, name='{name}', children={len(self.children)})"

    
class ObjectLayer(RenderLayer):
    """Render layer that stores object label information with depth-based allocation."""
    
    def __init__(self, id, constant_size=None, max_depth=5, labels_per_depth=50):
        super().__init__(id, dtype=np.uint16, constant_size=constant_size, num_channels=1)
        # Tree structure: label -> TreeNode (unified structure)
        self.label_map = {}  # label -> TreeNode
        self.window_roots = {}  # hwnd -> TreeNode (root node for each window)
        
        # Window tracking for z-order and root management
        self.window_z_orders = {}  # hwnd -> z-order position (0 = topmost)
        self.window_labels = {}  # hwnd -> window label (depth 0)
        
        # Configurable depth-based label allocation
        self.max_depth = max_depth  # Maximum supported depth levels (configurable)
        self.labels_per_depth = labels_per_depth  # Fixed allocation per depth level (configurable)
        self.depth_counters = {}  # depth_level -> next available label in that depth's range
        
        # Fast lookup arrays for efficient fill_object_region
        max_labels = (max_depth + 1) * labels_per_depth
        self.label_to_hwnd = np.zeros(max_labels + 1, dtype=np.int64)  # label -> hwnd for O(1) lookup (int64 for 64-bit handles)
        self.label_to_zorder = np.full(max_labels + 1, 999, dtype=np.int16)  # label -> z-order for O(1) lookup
        
    def _get_or_create_window_label(self, hwnd, window_obj=None):
        """Get or create label for window at depth level 0.
        
        Args:
            hwnd: Window handle
            window_obj: Optional NVDA window object to store
        
        Returns:
            int: Window label (depth 0)
        """
        from .utils import get_window_rect, update_window_z_orders
        
        # Check if window already has a label
        if hwnd in self.window_labels:
            return self.window_labels[hwnd]
        
        # Add window to z-order tracking and get actual z-order
        if hwnd not in self.window_z_orders:
            self.window_z_orders[hwnd] = 999  # Temporary value before update
            update_window_z_orders(self.window_z_orders)
            self.update_z_order_lookups()
        
        # Allocate label for window at depth level 0
        window_label = self._calculate_label_for_depth(0)
        if window_label == 0:
            logMessage(f"[ObjectLayer] Failed to allocate label for window {hwnd}")
            return 0
        
        # Create window object info
        window_info = {
            'name': f'Window {hwnd}',
            'role': 15,  # Default to WINDOW role (15 in NVDA controlTypes)
            'hwnd': hwnd
        }
        
        if window_obj:
            window_info['name'] = window_obj.name if hasattr(window_obj, 'name') else f'Window {hwnd}'
            window_info['role'] = window_obj.role if hasattr(window_obj, 'role') else 15
            window_info['object'] = window_obj
        
        # Get window location
        window_rect = get_window_rect(hwnd)
        if window_rect:
            window_location = Rect(window_rect.left, window_rect.top, 
                                  width=window_rect.width, height=window_rect.height)
        else:
            window_location = None
        
        # Create TreeNode for window (root node at depth 0)
        window_node = TreeNode(
            label=window_label,
            obj_info=window_info,
            location=window_location,
            depth=0,
            hwnd=hwnd,
            mesh_point=None
        )
        
        # Store node in label_map
        self.label_map[window_label] = window_node
        self.window_roots[hwnd] = window_node
        
        # Update fast lookup arrays with actual z-order
        if window_label < len(self.label_to_hwnd):
            self.label_to_hwnd[window_label] = hwnd
            self.label_to_zorder[window_label] = self.window_z_orders.get(hwnd, 999)
        
        self.window_labels[hwnd] = window_label
        
        # Add to emulator debug log
        if self.plugin:
            window_name = window_info.get('name', f'Window {hwnd}') or f'Window {hwnd}'
            role = window_info.get('role')
            
            # Get human-readable role name
            if role is not None:
                try:
                    role_str = controlTypes.Role(role).displayString
                except:
                    try:
                        role_str = controlTypes.roleLabels.get(role, f"Unknown({role})")
                    except:
                        role_str = str(role)
            else:
                role_str = "Window"
            
            # Ensure role_str is a string
            role_str = str(role_str) if role_str else "Window"
            
            # Convert location to bbox string
            if window_location and hasattr(window_location, 'left'):
                bbox = f"({window_location.left}, {window_location.top}, {window_location.width}, {window_location.height})"
            else:
                bbox = "N/A"
            
            # Check if this is a desktop/shell window
            from .utils import is_desktop_or_shell_window
            if is_desktop_or_shell_window(hwnd):
                logMessage(f"[ObjectLayer] Created desktop/shell window label {window_label} for hwnd {hwnd}, bbox={bbox}")
            
            self.plugin.hardware.add_label_to_debug(window_label, window_name, role_str, "N/A", bbox)
        
        return window_label
    
    def _is_actually_window(self, obj, obj_info, obj_hwnd, location_rect):
        """Check if object is actually a window, not just based on role.
        
        Verifies:
        1. Role is WINDOW (15) or DIALOG (16)
        2. Object location approximately matches the window bounds
        
        Args:
            obj: NVDA object
            obj_info: Object information dict
            obj_hwnd: Window handle
            location_rect: Object location as Rect
            
        Returns:
            bool: True if this is actually the window object
        """
        try:
            from .utils import get_window_rect
            
            obj_role = obj_info.get('role')
            
            # Must have window role
            if obj_role not in (15, 16):
                return False
            
            # If we can't get location, can't verify
            if not location_rect or not obj_hwnd:
                return False
            
            # Get actual window bounds
            window_rect = get_window_rect(obj_hwnd)
            if not window_rect:
                return False
            
            # Check if object bounds approximately match window bounds
            # Allow small tolerance for window decorations
            tolerance = 10
            width_match = abs(location_rect.width - window_rect.width) <= tolerance
            height_match = abs(location_rect.height - window_rect.height) <= tolerance
            left_match = abs(location_rect.left - window_rect.left) <= tolerance
            top_match = abs(location_rect.top - window_rect.top) <= tolerance
            
            is_match = width_match and height_match and left_match and top_match
            
            # Don't log - this is expected for list items and other role=15 non-windows
            # if not is_match:
            #     logMessage(f"[ObjectLayer] Object '{obj_info.get('name', 'Unknown')[:30]}' has window role but bounds don't match: obj={location_rect}, window={window_rect}")
            
            return is_match
        except Exception as e:
            logMessage(f"[ObjectLayer] _is_actually_window error for '{obj_info.get('name', 'Unknown')[:20]}': {e}")
            return False
    
    def update_z_order_lookups(self):
        """Update the label_to_zorder lookup array when window z-orders change.
        
        This should be called after window_z_orders dict is updated (e.g., on focus change).
        """
        # Update z-order for all labels belonging to tracked windows
        for label, hwnd in enumerate(self.label_to_hwnd):
            if hwnd > 0:  # Skip background (0) and unallocated labels
                self.label_to_zorder[label] = self.window_z_orders.get(hwnd, 999)
    
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
    
    def _check_duplicate_object(self, obj_info, obj_hwnd, location_rect, check_window_validation=True):
        """Check if object already exists in label map.
        
        Args:
            obj_info: Object information dictionary
            obj_hwnd: Window handle
            location_rect: Object location as Rect
            check_window_validation: If False, use simple role check instead of bounds validation
            
        Returns:
            int: Existing label if duplicate found, 0 otherwise
        """
        if location_rect is None:
            return 0
        
        # Determine if incoming object is a window
        # During reacquisition, use fast role-based check to avoid performance issues
        if check_window_validation:
            obj = obj_info.get('object')
            is_incoming_window = self._is_actually_window(obj, obj_info, obj_hwnd, location_rect)
        else:
            # Fast path for reacquisition - just check role
            incoming_role = obj_info.get('role')
            is_incoming_window = incoming_role in (15, 16) if incoming_role is not None else False
        
        for existing_label, existing_node in self.label_map.items():
            if existing_node.hwnd != obj_hwnd:
                continue  # Different window
            
            existing_obj_info = existing_node.obj_info
            existing_depth = existing_node.depth
            
            # Don't match non-windows with depth-0 objects (windows)
            if not is_incoming_window and existing_depth == 0:
                continue
            
            # Don't match windows with non-depth-0 objects
            if is_incoming_window and existing_depth != 0:
                continue
            
            # Check name and role match
            if (existing_obj_info.get('name') != obj_info.get('name') or
                existing_obj_info.get('role') != obj_info.get('role')):
                continue
            
            # Compare locations exactly (no tolerance)
            existing_location = existing_node.location
            if existing_location is None:
                continue
            
            # Check if locations are exactly equal
            if (existing_location.left == location_rect.left and
                existing_location.top == location_rect.top and
                existing_location.width == location_rect.width and
                existing_location.height == location_rect.height):
                # Found duplicate
                return existing_label
        
        return 0
    

    
    def _insert_node_into_tree(self, new_node):
        """Insert a node into the tree, finding its parent or children and updating depths.
        
        This method handles both cases:
        1. Parent already exists -> Insert as child of parent
        2. Children exist at default depth -> Adopt children and update their depths recursively
        
        Args:
            new_node: TreeNode to insert into the tree
        """
        hwnd = new_node.hwnd
        
        # Case 1: Try to find a parent for this node
        parent_node = self._find_parent_node(new_node)
        
        if parent_node:
            # Found a parent - add this node as child and update depth based on parent
            parent_node.add_child(new_node)
            new_node.update_depth_recursively(parent_node.depth + 1, self)
            
            # Check if any of the parent's other children should actually be our children
            # This handles re-adoption when a removed parent comes back
            siblings_to_adopt = []
            for sibling in parent_node.children[:]:  # Copy list to avoid modification issues
                if sibling.label == new_node.label:
                    continue  # Skip self
                
                # Check if this new node should be the parent of this sibling
                if new_node.contains(sibling):
                    siblings_to_adopt.append(sibling)
            
            # Re-adopt siblings that belong to this node
            if siblings_to_adopt:
                for sibling in siblings_to_adopt:
                    # Remove from parent (grandparent for these nodes)
                    parent_node.remove_child(sibling)
                    # Add as child of this node
                    new_node.add_child(sibling)
                    # Update depth recursively
                    sibling.update_depth_recursively(new_node.depth + 1, self)
                
                logMessage(f"[ObjectLayer] Node {new_node.label} re-adopted {len(siblings_to_adopt)} siblings from parent {parent_node.label}")
            
            logMessage(f"[ObjectLayer] Inserted node {new_node.label} as child of {parent_node.label}, depth={new_node.depth}")
        else:
            # Case 2: No parent found - check if this node should adopt any existing children
            children_to_adopt = self._find_children_nodes(new_node)
            
            if children_to_adopt:
                # This node is a parent to some existing nodes - adopt them and update depths
                for child_node in children_to_adopt:
                    # Remove from current parent if it has one
                    if child_node.parent:
                        child_node.parent.remove_child(child_node)
                    
                    # Add as child of this node
                    new_node.add_child(child_node)
                
                # Recursively update depths starting from this node
                # If this node has a parent, use parent.depth + 1, otherwise use current depth
                if new_node.parent:
                    new_node.update_depth_recursively(new_node.parent.depth + 1, self)
                else:
                    # No parent, so keep current depth and update children
                    for child_node in new_node.children:
                        child_node.update_depth_recursively(new_node.depth + 1, self)
                
                logMessage(f"[ObjectLayer] Node {new_node.label} adopted {len(children_to_adopt)} children, depth={new_node.depth}")
            else:
                # No parent and no children - standalone node or direct child of window
                logMessage(f"[ObjectLayer] Inserted standalone node {new_node.label}, depth={new_node.depth}")
        
        # Store node in label_map
        self.label_map[new_node.label] = new_node
        
        # If this is a window (depth 0), store as root
        if new_node.depth == 0:
            self.window_roots[hwnd] = new_node
    
    def _reallocate_node_label(self, node, new_depth):
        """Reallocate a node's label when its depth changes.
        
        Args:
            node: TreeNode to reallocate
            new_depth: New depth level for the node
        """
        old_label = node.label
        old_depth = node.depth
        
        # Allocate new label from the new depth range
        new_label = self._calculate_label_for_depth(new_depth)
        
        if new_label == 0:
            logMessage(f"[ObjectLayer] ERROR: Failed to reallocate label for node {old_label} at new depth {new_depth}")
            return
        
        # Update pixels in the image (remap old label to new label)
        if self.image.size > 0:
            self.image[self.image == old_label] = new_label
        
        # Update node properties
        node.label = new_label
        node.depth = new_depth
        
        # Update label_map (remove old entry, add new entry)
        if old_label in self.label_map:
            del self.label_map[old_label]
        self.label_map[new_label] = node
        
        # Update fast lookup arrays
        if old_label < len(self.label_to_hwnd):
            self.label_to_hwnd[old_label] = 0
            self.label_to_zorder[old_label] = 999
        
        if new_label < len(self.label_to_hwnd):
            self.label_to_hwnd[new_label] = node.hwnd
            self.label_to_zorder[new_label] = self.window_z_orders.get(node.hwnd, 999)
        
        # Update window_labels if this is a window
        if old_depth == 0 and node.hwnd in self.window_labels:
            if self.window_labels[node.hwnd] == old_label:
                self.window_labels[node.hwnd] = new_label
        elif new_depth == 0:  # Became a window
            self.window_labels[node.hwnd] = new_label
        
        # Update debug log
        if self.plugin:
            self.plugin.hardware.remove_label_from_debug(old_label)
            
            obj_name = node.obj_info.get('name', 'Unknown') or 'Unknown'
            obj = node.obj_info.get('object')
            obj_value = getattr(obj, 'value', 'N/A') if obj else 'N/A'
            obj_value = str(obj_value) if obj_value else 'N/A'
            role = node.obj_info.get('role')
            
            # Get human-readable role name
            if role is not None:
                try:
                    role_str = controlTypes.Role(role).displayString
                except:
                    try:
                        role_str = controlTypes.roleLabels.get(role, f"Unknown({role})")
                    except:
                        role_str = str(role)
            else:
                role_str = "Unknown"
            
            role_str = str(role_str) if role_str else "Unknown"
            
            # Convert location to bbox string
            if node.location and hasattr(node.location, 'left'):
                bbox = f"({node.location.left}, {node.location.top}, {node.location.width}, {node.location.height})"
            else:
                bbox = str(node.location) if node.location else "N/A"
            
            self.plugin.hardware.add_label_to_debug(new_label, obj_name, role_str, obj_value, bbox)
        
        # Recycle the old label position by resetting depth counter if this label is earlier
        if old_depth in self.depth_counters:
            # If this freed label is less than the current counter, reset to reuse it
            if old_label < self.depth_counters[old_depth]:
                self.depth_counters[old_depth] = old_label
        
        logMessage(f"[ObjectLayer] Reallocated node label {old_label} -> {new_label} (depth {old_depth} -> {new_depth})")
    
    def _find_parent_node(self, node):
        """Find the parent node for a given node by spatial containment.
        
        Args:
            node: TreeNode to find parent for
            
        Returns:
            TreeNode: Parent node if found, None otherwise
        """
        if not node.location:
            return None
        
        # Find smallest existing node that contains this node (same window)
        smallest_parent = None
        smallest_area = float('inf')
        
        for candidate_label, candidate_node in self.label_map.items():
            if candidate_node.hwnd != node.hwnd:
                continue  # Different window
            
            # Skip self
            if candidate_node.label == node.label:
                continue
            
            # Check if candidate contains this node
            if candidate_node.contains(node):
                candidate_area = candidate_node.location.area()
                if candidate_area < smallest_area:
                    smallest_area = candidate_area
                    smallest_parent = candidate_node
        
        return smallest_parent
    
    def _find_children_nodes(self, node):
        """Find any existing nodes that should be children of this node.
        
        Looks for nodes in the same window that are spatially contained by this node.
        Only considers nodes at their current depth level (not already assigned as deep descendants).
        
        Args:
            node: TreeNode to find children for
            
        Returns:
            list: List of TreeNode objects that should be children
        """
        if not node.location:
            return []
        
        children = []
        
        for candidate_label, candidate_node in self.label_map.items():
            if candidate_node.hwnd != node.hwnd:
                continue  # Different window
            
            # Skip self
            if candidate_node.label == node.label:
                continue
            
            # Skip if candidate is already our ancestor (would create cycle)
            current = node.parent
            while current:
                if current.label == candidate_node.label:
                    break  # Candidate is our ancestor, skip
                current = current.parent
            else:
                # Check if this node contains the candidate
                if node.contains(candidate_node):
                    children.append(candidate_node)
        
        return children
    
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
        
        # Check if this object is actually a window (not just by role, but by bounds matching)
        obj_role = obj_info.get('role')
        obj_name = obj_info.get('name', 'Unknown')
        is_window = self._is_actually_window(obj, obj_info, obj_hwnd, location_rect)
        
        # Windows are always depth 0
        if is_window:
            object_depth_level = 0
        else:
            # For non-window objects, ensure the window has a label at depth 0
            window_label = self._get_or_create_window_label(obj_hwnd, window_obj=None)
            if window_label == 0:
                logMessage(f"[ObjectLayer] add_label: Failed to create window label for hwnd {obj_hwnd}")
                return 0
            
            # Default to depth 1 - tree insertion will find parent and update depth if needed
            object_depth_level = 1
        
        # Allocate label from the depth range
        allocated_label = self._calculate_label_for_depth(object_depth_level)
        
        # Verify label is valid
        if allocated_label == 0:
            logMessage(f"[ObjectLayer] add_label: Failed to allocate label for depth level {object_depth_level}")
            return 0
        
        # FINAL SAFETY CHECK: Verify non-windows are not getting depth-0 labels
        if not is_window and allocated_label <= 50:  # Depth 0 labels are 1-50
            logMessage(f"[ObjectLayer] ERROR: Non-window '{obj_name[:30]}' (role={obj_role}) got depth-0 label {allocated_label}! Forcing to depth 1.")
            # Force recalculation for depth 1
            allocated_label = self._calculate_label_for_depth(1)
            object_depth_level = 1
            if allocated_label == 0:
                logMessage(f"[ObjectLayer] add_label: Failed to allocate depth-1 label")
                return 0
        
        # Verify label is valid
        if allocated_label == 0:
            logMessage(f"[ObjectLayer] add_label: Failed to allocate label for depth level {object_depth_level}")
            return 0
        
        # Create TreeNode and insert into tree structure
        # The tree insertion will handle finding parent/children and updating depths
        new_node = TreeNode(
            label=allocated_label,
            obj_info=obj_info,
            location=location_rect,
            depth=object_depth_level,
            hwnd=obj_hwnd,
            mesh_point=mesh_point
        )
        
        # Insert node into tree - this will find parent or children and update depths recursively
        self._insert_node_into_tree(new_node)
        
        # Update fast lookup arrays
        if allocated_label < len(self.label_to_hwnd):
            self.label_to_hwnd[allocated_label] = obj_hwnd
            self.label_to_zorder[allocated_label] = self.window_z_orders.get(obj_hwnd, 999)
        
        # Add to emulator debug log
        if self.plugin:
            obj_name = obj_info.get('name', 'Unknown') or 'Unknown'
            obj = obj_info.get('object')
            obj_value = getattr(obj, 'value', 'N/A') if obj else 'N/A'
            obj_value = str(obj_value) if obj_value else 'N/A'
            role = obj_info.get('role')
            
            # Get human-readable role name
            if role is not None:
                try:
                    role_str = controlTypes.Role(role).displayString
                except:
                    try:
                        role_str = controlTypes.roleLabels.get(role, f"Unknown({role})")
                    except:
                        role_str = str(role)
            else:
                role_str = "Unknown"
            
            # Ensure role_str is a string
            role_str = str(role_str) if role_str else "Unknown"
            
            # Convert location to bbox string
            if location_rect and hasattr(location_rect, 'left'):
                bbox = f"({location_rect.left}, {location_rect.top}, {location_rect.width}, {location_rect.height})"
            else:
                bbox = str(location_rect) if location_rect else "N/A"
            
            self.plugin.hardware.add_label_to_debug(allocated_label, obj_name, role_str, obj_value, bbox)
        
        return allocated_label
    
    def _get_depth_range(self, depth_level):
        """Get the (start_label, end_label) range for a depth level.
        
        Args:
            depth_level: Depth level (0 = windows, 1 = direct children, 2+ = descendants)
        
        Returns:
            Tuple of (start_label, end_label) or None if depth level exceeds max
        """
        if depth_level > self.max_depth:
            return None  # Depth level exceeds maximum
        
        # Calculate range for this depth level
        # Depth 0: 1 to labels_per_depth
        # Depth 1: labels_per_depth+1 to 2*labels_per_depth
        # etc.
        start_label = (depth_level * self.labels_per_depth) + 1
        end_label = (depth_level + 1) * self.labels_per_depth
        
        return (start_label, end_label)
    
    def _calculate_label_for_depth(self, depth_level):
        """Allocate label from depth level range.
        
        Global depth ranges shared by all windows:
        - Depth level 0: 1 to labels_per_depth (windows)
        - Depth level 1: labels_per_depth+1 to 2*labels_per_depth (direct children)
        - etc.
        
        For example, with labels_per_depth=50 and max_depth=5:
        D0=[1-50], D1=[51-100], D2=[101-150], D3=[151-200], D4=[201-250], D5=[251-300]
        
        Args:
            depth_level: The depth level for which to allocate a label (0 to max_depth)
        
        Returns:
            int: Label for the new object, or 0 if allocation failed
        """
        # Get range for this depth level
        depth_range = self._get_depth_range(depth_level)
        if depth_range is None:
            logMessage(f"[ERROR] Depth level {depth_level} exceeds max_depth {self.max_depth}")
            return 0
        
        start_label, end_label = depth_range
        
        # Initialize counter for this depth level if needed
        if depth_level not in self.depth_counters:
            self.depth_counters[depth_level] = start_label
        
        # Get next available label from this depth level range
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
            dict: Statistics including global depth ranges and per-window usage
        """
        stats = {
            'max_depth': self.max_depth,
            'labels_per_depth': self.labels_per_depth,
            'depth_levels': {},
            'windows': {}
        }
        
        # Global depth level statistics
        for depth_level in range(0, self.max_depth + 1):
            depth_range = self._get_depth_range(depth_level)
            if depth_range:
                start, end = depth_range
                current_counter = self.depth_counters.get(depth_level, start)
                used_in_range = current_counter - start
                available = end - current_counter + 1
                
                # Count actual objects at this depth
                count = sum(1 for node in self.label_map.values() if node.depth == depth_level)
                
                stats['depth_levels'][depth_level] = {
                    'range': (start, end),
                    'next_label': current_counter,
                    'allocated': used_in_range,
                    'available': available,
                    'active_objects': count
                }
        
        # Per-window statistics
        for hwnd, window_label in self.window_labels.items():
            window_stats = {
                'window_label': window_label,
                'z_order': self.window_z_orders.get(hwnd, 999),
                'total_objects': sum(1 for node in self.label_map.values() if node.hwnd == hwnd)
            }
            stats['windows'][hwnd] = window_stats
        
        return stats
    
    def remove_label(self, label):
        """Remove a label and recycle its position in the depth range."""
        if label not in self.label_map:
            return False
        
        # Remove from emulator debug log
        if self.plugin:
            self.plugin.hardware.remove_label_from_debug(label)
        
        # Get node before removing
        node = self.label_map[label]
        label_depth = node.depth
        label_hwnd = node.hwnd
        
        # If this node has children, reassign them to this node's parent but keep their current depth
        if node.children:
            for child in node.children[:]:  # Copy list to avoid modification during iteration
                if node.parent:
                    # Reassign child to this node's parent (grandparent)
                    node.parent.add_child(child)
                    # Keep child at current depth (don't move down)
                    logMessage(f"[ObjectLayer] Reassigned child {child.label} (depth {child.depth}) to grandparent {node.parent.label} after parent {label} removed")
                else:
                    # This node has no parent, so children become orphans (direct window children)
                    child.parent = None
                    # Keep child at current depth (don't move to depth 1)
                    logMessage(f"[ObjectLayer] Child {child.label} orphaned at depth {child.depth} after parent {label} removed")
        
        # Remove from parent's children list
        if node.parent:
            node.parent.remove_child(node)
        
        # If this was a window root, remove from window_roots
        if label_depth == 0 and label_hwnd in self.window_roots:
            if self.window_roots[label_hwnd].label == label:
                del self.window_roots[label_hwnd]
        
        # Clear pixels for this node
        self.image[self.image == label] = 0
        
        # Remove from label map
        del self.label_map[label]
        
        # Clear from fast lookup arrays
        if label < len(self.label_to_hwnd):
            self.label_to_hwnd[label] = 0
            self.label_to_zorder[label] = 999
        
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
                    #logMessage(f"[ObjectLayer] Recycled label {label} at depth {label_depth} for window {label_hwnd}, reset counter")
        
        return True
    
    def fill_object_region(self, label, location, region):
        """Fill object region with label using efficient vectorized z-order priority.
        
        Priority order:
        1. Windows in front (lower z-order) always overwrite windows behind (higher z-order)
        2. Within same window: Higher labels (deeper) overwrite lower labels (shallower)
        
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
                
        # Fill object region with efficient vectorized z-order comparison
        if obj_clamped and obj_clamped.width > 0 and obj_clamped.height > 0:
            x1, y1, x2, y2 = obj_clamped.left, obj_clamped.top, obj_clamped.right, obj_clamped.bottom
            region_slice = self.image[y1:y2, x1:x2]
            
            # Get current label's window and z-order from fast lookup
            if label >= len(self.label_to_hwnd):
                logMessage(f"[ObjectLayer] fill_object_region: label {label} out of bounds")
                return
            current_hwnd = self.label_to_hwnd[label]
            current_z_order = self.label_to_zorder[label]
            
            # Verify label is valid
            if current_hwnd == 0:
                # Label not in lookup array - this shouldn't happen, but handle gracefully
                label_data = self.label_map.get(label)
                if not label_data:
                    logMessage(f"[ObjectLayer] fill_object_region: label {label} not found in label_map")
                    return
                # Log for debugging
                logMessage(f"[ObjectLayer] fill_object_region: label {label} has hwnd=0 in lookup, recovering from label_map")
                current_hwnd = label_data.hwnd
                current_z_order = self.window_z_orders.get(current_hwnd, 999)
            
            # Vectorized lookup: get hwnd and z-order for all existing labels in region
            existing_hwnds = self.label_to_hwnd[region_slice]
            existing_z_orders = self.label_to_zorder[region_slice]
            
            # Build overwrite mask using vectorized operations
            # Always overwrite background (label 0)
            overwrite_mask = (region_slice == 0)
            
            # Same window: higher label overwrites lower
            same_window = (existing_hwnds == current_hwnd) & (region_slice > 0)
            overwrite_mask |= same_window & (region_slice < label)
            
            # Different window: check z-order if available, otherwise use label comparison
            different_window = (existing_hwnds != current_hwnd) & (region_slice > 0)
            
            # Only use z-order when both windows have valid z-orders (< 999)
            both_have_valid_z_order = (current_z_order < 999) & (existing_z_orders < 999)
            z_order_condition = different_window & both_have_valid_z_order & (current_z_order < existing_z_orders)
            
            # For windows without valid z-orders, fall back to label comparison
            # (higher label overwrites lower, similar to depth-based ordering)
            no_valid_z_order = different_window & ~both_have_valid_z_order
            label_fallback_condition = no_valid_z_order & (region_slice < label)
            
            overwrite_mask |= z_order_condition | label_fallback_condition
            
            # Apply the overwrite mask
            self.image[y1:y2, x1:x2] = np.where(overwrite_mask, label, region_slice)
        