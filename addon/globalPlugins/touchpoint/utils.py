import logHandler
import controlTypes
import winUser
import ctypes
from .dependencies import np

class Rect:
    """Simple rectangle class to represent object locations.
    """
    def __init__(self, left, top, right=None, bottom=None, width=None, height=None):
        """Generates rectangle coordinates from either left/top/right/bottom (default) or left/top/width/height."""
        self.left = left
        self.top = top
        self.right = right if right is not None else left + width if width is not None else left
        self.bottom = bottom if bottom is not None else top + height if height is not None else top
        self.width = self.right - self.left
        self.height = self.bottom - self.top
        
    def copy(self):
        """Create a copy of this Rect."""
        return Rect(self.left, self.top, self.right, self.bottom)
    
    def area(self):
        """Calculate the area of the rectangle."""
        return max(0, self.width) * max(0, self.height)
    
    def perimeter(self):
        """Calculate the perimeter of the rectangle."""
        return 2 * (max(0, self.width) + max(0, self.height))
        
    def intersection(self, other):
        """Calculate the intersection of this rectangle with another.
        
        Args:
            other (Rect): Another rectangle to intersect with.
        
        Returns:
            Rect: A new Rect representing the intersection area, or None if no intersection.
        """
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        
        if left < right and top < bottom:
            return Rect(left, top, right, bottom)
        return None
    
    def union(self, other):
        """Calculate the union of this rectangle with another.
        
        Args:
            other (Rect): Another rectangle to union with.
        
        Returns:
            Rect: A new Rect representing the union area.
        """
        left = min(self.left, other.left)
        top = min(self.top, other.top)
        right = max(self.right, other.right)
        bottom = max(self.bottom, other.bottom)
        
        return Rect(left, top, right, bottom)
    
    def iou(self, other):
        """Calculate the Intersection over Union (IoU) with another rectangle.
        
        Args:
            other (Rect): Another rectangle to compare with.
        
        Returns:
            float: IoU value between 0 and 1, or 0 if no intersection.
        """
        intersection = self.intersection(other)
        if not intersection:
            return 0.0
        
        intersection_area = intersection.area()
        union_area = self.area() + other.area() - intersection_area
        
        return intersection_area / union_area if union_area > 0 else 0.0
    
    def contains(self, other):
        """Check if this rectangle completely contains another.
        
        Args:
            other (Rect): Another rectangle to check.
        
        Returns:
            bool: True if this rectangle contains the other, False otherwise.
        """
        return (self.left <= other.left and
                self.top <= other.top and
                self.right >= other.right and
                self.bottom >= other.bottom)
        
    def intersects(self, other):
        """Check if this rectangle intersects with another.
        
        Args:
            other (Rect): Another rectangle to check.
        
        Returns:
            bool: True if the rectangles intersect, False otherwise.
        """
        return not (self.right <= other.left or
                    self.left >= other.right or
                    self.bottom <= other.top or
                    self.top >= other.bottom)
    
    def inside(self, other):
        """Check if this rectangle is completely inside another.
        
        Args:
            other (Rect): Another rectangle to check.
        
        Returns:
            bool: True if this rectangle is inside the other, False otherwise.
        """
        return (self.left >= other.left and
                self.top >= other.top and
                self.right <= other.right and
                self.bottom <= other.bottom)
    
    def top_left(self):
        """Get the top-left corner coordinates."""
        return (self.left, self.top)
    
    def bottom_right(self):
        """Get the bottom-right corner coordinates."""
        return (self.right, self.bottom)
    
    def shape(self):
        """Get the shape of the rectangle as (width, height)."""
        return (self.width, self.height)
        
    def global_to_local(self, global_point):
        """Get rectangle coordinates relative to a global point (e.g., screen origin)."""
        return Rect(
            self.left - global_point[0],
            self.top - global_point[1],
            self.right - global_point[0],
            self.bottom - global_point[1]
        )
        
    def local_to_global(self, local_point):
        """Get rectangle coordinates relative to a local point (e.g., window origin)."""
        return Rect(
            self.left + local_point[0],
            self.top + local_point[1],
            self.right + local_point[0],
            self.bottom + local_point[1]
        )
        
    def pad(self, padding):
        """Expand the rectangle by a certain padding on all sides."""
        return Rect(
            self.left - padding,
            self.top - padding,
            self.right + padding,
            self.bottom + padding
        )
        
    def __repr__(self):
        return f"Rect({self.left}, {self.top}, {self.right}, {self.bottom})"
    
    def __eq__(self, other):
        """Check if two rectangles are equal by comparing their coordinates."""
        if not isinstance(other, Rect):
            return False
        return (self.left == other.left and
                self.top == other.top and
                self.right == other.right and
                self.bottom == other.bottom)
    
    def __hash__(self):
        """Make Rect hashable so it can be used in sets and as dict keys."""
        return hash((self.left, self.top, self.right, self.bottom))

def logMessage(message):
        """Log a message to the NVDA log.
        """
        logHandler.log.info(message)

def logUIElement(obj, eventName):
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
        
        logMessage(f"Event: {eventName} | Name: {name} | Role: {roleName}")
        
        # You can extend this to send data to external systems
        # For example: send to serial port, TCP socket, or save to file
        
    except Exception as e:
        logMessage(f"Error logging UI element: {str(e)}")

def is_desktop_or_shell_window(hwnd):
    """Check if a window is a desktop or shell window.
    
    Desktop/shell windows should not occlude other windows.
    
    Args:
        hwnd: Window handle
        
    Returns:
        bool: True if desktop/shell window
    """
    try:
        if not hwnd:
            return False
        
        # Check window class name
        class_name = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, class_name, 256)
        class_name_str = class_name.value
        
        # Known desktop/shell window classes
        desktop_classes = ['Progman', 'WorkerW', 'Shell_TrayWnd', 'DV2ControlHost']
        if class_name_str in desktop_classes:
            return True
        
        return False
    except:
        return False


def get_window_z_order(hwnd):
    """Get the z-order position of a window (0 = topmost).
    
    NOTE: This counts ALL windows above the given window in the z-order stack,
    not just tracked windows. For relative comparison between specific windows,
    use get_relative_z_orders() instead.
    
    Args:
        hwnd: Window handle (HWND)
    
    Returns:
        int: Z-order position (0 = topmost, higher values = behind others)
        Returns -1 if window not found or error occurs
    """
    try:
        if not hwnd:
            return -1
        
        # GW_HWNDPREV = 3 (get window above this one in z-order)
        GW_HWNDPREV = 3
        
        z_order = 0
        current_hwnd = hwnd
        
        # Count how many windows are above this one
        while True:
            prev_hwnd = winUser.getWindow(current_hwnd, GW_HWNDPREV)
            if not prev_hwnd:
                break
            z_order += 1
            current_hwnd = prev_hwnd
            
            # Prevent infinite loop (safety check) - increased limit
            if z_order > 500:
                logMessage(f"[get_window_z_order] Exceeded 500 windows for hwnd {hwnd}, stopping")
                return -1
        
        return z_order
    except Exception as e:
        logMessage(f"[get_window_z_order] Error for hwnd {hwnd}: {e}")
        return -1

def get_object_window_handle(obj):
    """Get the window handle from an NVDA object.
    
    Args:
        obj: NVDA object
    
    Returns:
        int: Window handle (HWND) or None if not available
    """
    try:
        # Try direct windowHandle attribute
        if hasattr(obj, 'windowHandle') and obj.windowHandle:
            return obj.windowHandle
        
        # Try going up to parent window
        current = obj
        depth = 0
        while current and depth < 50:  # Prevent infinite loop
            if hasattr(current, 'windowHandle') and current.windowHandle:
                return current.windowHandle
            current = current.parent if hasattr(current, 'parent') else None
            depth += 1
        
        return None
    except Exception as e:
        logMessage(f"Error getting object window handle: {e}")
        return None

def get_window_rect(hwnd):
    """Get the window rectangle.
    
    Args:
        hwnd: Window handle (HWND)
    
    Returns:
        Rect object
    """
    try:
        if not hwnd:
            return None
        
        # Use ctypes to call GetWindowRect from user32.dll
        class RECT(ctypes.Structure):
            _fields_ = [('left', ctypes.c_long),
                       ('top', ctypes.c_long),
                       ('right', ctypes.c_long),
                       ('bottom', ctypes.c_long)]
        
        rect = RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return Rect(rect.left, rect.top, rect.right, rect.bottom)
        return None
    except Exception as e:
        logMessage(f"Error getting window rect: {e}")
        return None

def get_actual_border_mask(rect, image_shape):
    """Generate a boolean mask for only the actual borders of a rectangle that fall within image bounds.
    
    This does not create artificial borders when the rectangle extends beyond the image.
    Only marks pixels that are on the rectangle's actual border lines.
    
    Args:
        rect: Rect object defining the area to mask (can extend beyond image bounds)
        image_shape: Tuple (height, width) of the image
    Returns:
        np.ndarray: Boolean mask with True for pixels on the actual border that are visible
    """
    mask = np.zeros(image_shape, dtype=bool)
    img_height, img_width = image_shape
    
    # Check each border independently
    # Left border (x = rect.left)
    if 0 <= rect.left < img_width:
        y_start = max(0, rect.top)
        y_end = min(img_height, rect.bottom)
        if y_start < y_end:
            mask[y_start:y_end, rect.left] = True
    
    # Right border (x = rect.right - 1)
    if 0 <= rect.right - 1 < img_width and rect.right > 0:
        y_start = max(0, rect.top)
        y_end = min(img_height, rect.bottom)
        if y_start < y_end:
            mask[y_start:y_end, rect.right - 1] = True
    
    # Top border (y = rect.top)
    if 0 <= rect.top < img_height:
        x_start = max(0, rect.left)
        x_end = min(img_width, rect.right)
        if x_start < x_end:
            mask[rect.top, x_start:x_end] = True
    
    # Bottom border (y = rect.bottom - 1)
    if 0 <= rect.bottom - 1 < img_height and rect.bottom > 0:
        x_start = max(0, rect.left)
        x_end = min(img_width, rect.right)
        if x_start < x_end:
            mask[rect.bottom - 1, x_start:x_end] = True
    
    return mask

def update_window_z_orders(window_z_orders):
    """Update z-order values for all tracked windows using absolute ordering.
    
    Gets the absolute z-order position for each window (counting windows above it),
    then normalizes to relative positions among tracked windows.
    Desktop/shell windows are assigned a high z-order so they don't occlude.
    
    Args:
        window_z_orders: Dictionary mapping hwnd -> z-order to update in-place
    """
    try:
        if not window_z_orders:
            return
        
        # Separate desktop/shell windows from regular windows
        desktop_windows = []
        regular_windows = []
        
        # Get absolute z-order for each tracked window
        absolute_z_orders = {}
        for hwnd in window_z_orders.keys():
            if is_desktop_or_shell_window(hwnd):
                desktop_windows.append(hwnd)
                # Assign very high z-order to desktop windows (always in back)
                window_z_orders[hwnd] = 9998
            else:
                regular_windows.append(hwnd)
                z_order = get_window_z_order(hwnd)
                if z_order >= 0:
                    absolute_z_orders[hwnd] = z_order
                else:
                    absolute_z_orders[hwnd] = 9999  # Invalid/hidden window
        
        # Sort regular windows by absolute z-order and assign relative positions
        sorted_windows = sorted(absolute_z_orders.items(), key=lambda x: x[1])
        
        # Assign relative z-orders (0 = frontmost among tracked regular windows)
        for relative_pos, (hwnd, absolute_pos) in enumerate(sorted_windows):
            if absolute_pos == 9999:
                window_z_orders[hwnd] = 999  # Mark as unknown
            else:
                window_z_orders[hwnd] = relative_pos
        
        # Log results
        valid_z_orders = {hwnd: z for hwnd, z in window_z_orders.items() if z < 999}
        unknown_count = sum(1 for z in window_z_orders.values() if z >= 999)
        desktop_count = len(desktop_windows)
        
        if valid_z_orders:
            logMessage(f"[update_window_z_orders] Valid z-orders: {valid_z_orders}, Desktop: {desktop_count}, Unknown: {unknown_count}")
        elif unknown_count > 0:
            logMessage(f"[update_window_z_orders] All {unknown_count} windows have unknown z-order")
        
    except Exception as e:
        logMessage(f"Error updating window z-orders: {e}")