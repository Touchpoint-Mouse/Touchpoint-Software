"""
Touchpoint Hardware Emulator GUI

A wx-based GUI application that emulates the Touchpoint hardware device.
Runs within the NVDA addon process and displays hardware state.
"""

import time
import threading
import math

import wx

from .dependencies import cv2, np
from .utils import logMessage


class TouchpointEmulatorGUI:
    """Hardware emulator GUI integrated with NVDA addon."""
    
    # Display constants
    DEPTH_MAP_WINDOW_SIZE = 50  # pixels around cursor to show in depth map
    
    def __init__(self, hardware_driver):
        """Initialize the emulator GUI.
        
        Args:
            hardware_driver: Reference to the HardwareDriver instance
        """
        self.hardware_driver = hardware_driver
        self.frame = None
        self.is_open = False
        
        # State variables
        self.hardware_connected = False
        self.current_elevation = 0.0  # 0.0-1.0 range
        self.target_elevation = 0.0  # 0.0-1.0 range
        self.max_elevation_speed = 1.0  # units per second (1 unit = full range)
        self.last_update_time = time.time()
        
        # Depth map data
        self.current_depth_map = None
        self.depth_map_lock = threading.Lock()
        
        # Update timer
        self.timer = None
        
        # Colormap settings - use cv2 colormaps
        self.set_colormap(cv2.COLORMAP_VIRIDIS)
    
    def set_colormap(self, colormap_cv2):
        """Set the colormap for all visualizations.
        
        Args:
            colormap_cv2: OpenCV colormap constant (e.g., cv2.COLORMAP_VIRIDIS, cv2.COLORMAP_JET)
        """
        self.colormap_cv2 = colormap_cv2
    
    def open_window(self):
        """Open the emulator window (called from NVDA keybind)."""
        if self.is_open and self.frame:
            # Window already open, just bring to front
            self.frame.Raise()
            return
        
        # Create window in main thread using wx.CallAfter
        wx.CallAfter(self._create_window)
    
    def _create_window(self):
        """Create the wx window."""
        try:
            self.frame = wx.Frame(None, title="Touchpoint Hardware Emulator", 
                                 size=(800, 900), style=wx.DEFAULT_FRAME_STYLE & ~wx.RESIZE_BORDER)
            
            self.is_open = True
            self.last_update_time = time.time()
            
            # Build GUI
            self._build_gui()
            
            # Handle window close
            self.frame.Bind(wx.EVT_CLOSE, self._on_close)
            
            # Start periodic updates (60 FPS)
            self.timer = wx.Timer(self.frame)
            self.frame.Bind(wx.EVT_TIMER, lambda evt: self._update_display(), self.timer)
            self.timer.Start(16)  # ~60 FPS
            
            # Show the window
            self.frame.Show()
        except Exception as e:
            logMessage(f"[ERROR] Failed to create emulator window: {e}")
            self.is_open = False
    
    def _build_gui(self):
        """Build the GUI interface."""
        panel = wx.Panel(self.frame)
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # Title
        title = wx.StaticText(panel, label="Touchpoint Hardware Emulator")
        title_font = wx.Font(16, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        title.SetFont(title_font)
        main_sizer.Add(title, 0, wx.ALL | wx.CENTER, 10)
        
        # Connection Status Section
        status_box = wx.StaticBox(panel, label="Hardware Status")
        status_sizer = wx.StaticBoxSizer(status_box, wx.VERTICAL)
        
        hardware_sizer = wx.BoxSizer(wx.HORIZONTAL)
        hardware_label = wx.StaticText(panel, label="Physical Hardware:")
        self.hardware_status_label = wx.StaticText(panel, label="Disconnected")
        self.hardware_status_label.SetForegroundColour(wx.RED)
        status_font = wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        self.hardware_status_label.SetFont(status_font)
        hardware_sizer.Add(hardware_label, 0, wx.ALL, 5)
        hardware_sizer.Add(self.hardware_status_label, 0, wx.ALL, 5)
        status_sizer.Add(hardware_sizer, 0, wx.ALL, 5)
        
        main_sizer.Add(status_sizer, 0, wx.ALL | wx.EXPAND, 10)
        
        # Top canvas row (elevation and depth map side by side)
        canvas_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        # Elevation Indicator Section (left side)
        elevation_box = wx.StaticBox(panel, label="Elevation Level")
        elevation_sizer = wx.StaticBoxSizer(elevation_box, wx.VERTICAL)
        
        # Horizontal layout for elevation panel and colormap scale
        elevation_row = wx.BoxSizer(wx.HORIZONTAL)
        
        self.elevation_panel = wx.Panel(panel, size=(250, 200))
        self.elevation_panel.SetBackgroundColour(wx.WHITE)
        self.elevation_panel.Bind(wx.EVT_PAINT, self._on_paint_elevation)
        elevation_row.Add(self.elevation_panel, 0, wx.ALL, 5)
        
        # Colormap scale indicator
        self.colormap_scale_panel = wx.Panel(panel, size=(30, 200))
        self.colormap_scale_panel.SetBackgroundColour(wx.WHITE)
        self.colormap_scale_panel.Bind(wx.EVT_PAINT, self._on_paint_colormap_scale)
        elevation_row.Add(self.colormap_scale_panel, 0, wx.ALL, 5)
        
        elevation_sizer.Add(elevation_row, 0, wx.ALL, 0)
        
        self.elevation_value_label = wx.StaticText(panel, label="0.0%")
        value_font = wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        self.elevation_value_label.SetFont(value_font)
        elevation_sizer.Add(self.elevation_value_label, 0, wx.ALL | wx.CENTER, 5)
        
        canvas_sizer.Add(elevation_sizer, 1, wx.ALL | wx.EXPAND, 5)
        
        # Depth Map Display Section (right side)
        depth_box = wx.StaticBox(panel, label="Depth Map (Cursor Region)")
        depth_sizer = wx.StaticBoxSizer(depth_box, wx.VERTICAL)
        
        self.depth_map_panel = wx.Panel(panel, size=(280, 200))
        self.depth_map_panel.SetBackgroundColour(wx.WHITE)
        self.depth_map_panel.Bind(wx.EVT_PAINT, self._on_paint_depth_map)
        depth_sizer.Add(self.depth_map_panel, 0, wx.ALL, 5)
        
        canvas_sizer.Add(depth_sizer, 1, wx.ALL | wx.EXPAND, 5)
        
        main_sizer.Add(canvas_sizer, 0, wx.ALL | wx.EXPAND, 5)
        
        # Vibration Events Section (log only, no canvas)
        vibration_box = wx.StaticBox(panel, label="Vibration Events")
        vibration_sizer = wx.StaticBoxSizer(vibration_box, wx.VERTICAL)
        
        self.vibration_log = wx.TextCtrl(panel, size=(560, 200), 
                                        style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP)
        mono_font = wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        self.vibration_log.SetFont(mono_font)
        self.vibration_log.SetBackgroundColour(wx.Colour(245, 245, 245))
        self.vibration_log.SetForegroundColour(wx.Colour(51, 51, 51))
        vibration_sizer.Add(self.vibration_log, 1, wx.ALL | wx.EXPAND, 5)
        
        main_sizer.Add(vibration_sizer, 1, wx.ALL | wx.EXPAND, 10)
        
        panel.SetSizer(main_sizer)
    
    def set_hardware_status(self, connected):
        """Update hardware connection status.
        
        Args:
            connected: True if hardware is connected, False otherwise
        """
        self.hardware_connected = connected
        if self.is_open and self.frame:
            wx.CallAfter(self._update_connection_status)
    
    def set_elevation(self, elevation):
        """Set target elevation.
        
        Args:
            elevation: Target elevation value (0.0-1.0)
        """
        self.target_elevation = elevation
    
    def set_elevation_speed(self, speed):
        """Set maximum elevation speed.
        
        Args:
            speed: Maximum speed in units per second
        """
        self.max_elevation_speed = speed
    
    def set_vibration(self, amplitude, frequency, duration):
        """Set vibration parameters.
        
        Args:
            amplitude: Vibration amplitude (0.0-1.0)
            frequency: Vibration frequency in Hz
            duration: Duration in pulses (0 for indefinite)
        """
        # Add to event log if window is open
        if self.is_open and self.frame:
            wx.CallAfter(self._add_vibration_log, amplitude, frequency, duration)
    
    def update_depth_map(self, depth_map):
        """Update the depth map display.
        
        Args:
            depth_map: Numpy array representing depth map around cursor
        """
        with self.depth_map_lock:
            self.current_depth_map = depth_map.copy() if depth_map is not None else None
    
    def _add_vibration_log(self, amplitude, frequency, duration):
        """Add vibration event to log."""
        try:
            current_time = time.time()
            timestamp = time.strftime("%H:%M:%S", time.localtime(current_time))
            milliseconds = int((current_time % 1) * 1000)
            timestamp_with_ms = f"{timestamp}.{milliseconds:03d}"
            dur_str = "indefinite" if duration == 0 else f"{duration}pulses"
            
            if amplitude == 0.0 or frequency == 0.0:
                log_entry = f"[{timestamp_with_ms}] Vibration stopped\n"
            else:
                log_entry = f"[{timestamp_with_ms}] Amp: {amplitude:.3f}  Freq: {frequency:6.1f}Hz  Dur: {dur_str}\n"
            
            self.vibration_log.AppendText(log_entry)
            
            # Limit log to last 100 lines
            line_count = self.vibration_log.GetNumberOfLines()
            if line_count > 100:
                # Remove first line
                first_line_end = self.vibration_log.XYToPosition(0, 1)
                self.vibration_log.Remove(0, first_line_end)
            
            # Scroll to the end to show the latest entry
            self.vibration_log.SetInsertionPointEnd()
        except Exception as e:
            logMessage(f"[ERROR] Failed to add vibration log: {e}")
    
    def _update_connection_status(self):
        """Update the connection status labels."""
        if self.hardware_connected:
            self.hardware_status_label.SetLabel("Connected")
            self.hardware_status_label.SetForegroundColour(wx.Colour(0, 128, 0))
        else:
            self.hardware_status_label.SetLabel("Disconnected")
            self.hardware_status_label.SetForegroundColour(wx.RED)
    
    def _update_display(self):
        """Periodic update of the display elements."""
        if not self.is_open or not self.frame:
            return
        
        current_time = time.time()
        delta_time = current_time - self.last_update_time
        self.last_update_time = current_time
        
        # Smoothly animate elevation towards target based on speed
        if abs(self.target_elevation - self.current_elevation) > 0.01:
            # Calculate max change for this frame (speed is in units/second, where 1 unit = full range)
            max_change = self.max_elevation_speed * delta_time
            
            # Move towards target
            diff = self.target_elevation - self.current_elevation
            if abs(diff) <= max_change:
                self.current_elevation = self.target_elevation
            else:
                self.current_elevation += max_change if diff > 0 else -max_change
        
        # Update displays
        self.elevation_value_label.SetLabel(f"{self.current_elevation*100:.1f}%")
        
        # Trigger repaints
        self.elevation_panel.Refresh()
        self.colormap_scale_panel.Refresh()
        self.depth_map_panel.Refresh()
    
    def _on_paint_elevation(self, event):
        """Draw the elevation water level indicator using OpenCV."""
        dc = wx.PaintDC(self.elevation_panel)
        width, height = self.elevation_panel.GetSize()
        
        try:
            # Calculate how many pixels the elevation should fill (from bottom)
            filled_height = int(self.current_elevation * height)
            
            # Create full gradient for entire height (255 at top to 0 at bottom)
            # This way, as elevation rises, we reveal brighter colors
            full_gradient = np.linspace(255, 0, height, dtype=np.uint8)
            full_gradient = np.tile(full_gradient.reshape(-1, 1), (1, width))
            
            # Apply colormap to the full gradient
            colored_full = cv2.applyColorMap(full_gradient, self.colormap_cv2)
            colored_rgb_full = cv2.cvtColor(colored_full, cv2.COLOR_BGR2RGB)
            
            # Start with a white background
            image_array = np.ones((height, width, 3), dtype=np.uint8) * 255
            
            if filled_height > 0:
                # Copy only the bottom portion of the gradient (the filled part)
                # Take the bottom filled_height rows from the full gradient
                image_array[height - filled_height:height, :, :] = colored_rgb_full[height - filled_height:height, :, :]
            
            # Convert to wx.Bitmap
            image = wx.Image(width, height)
            image.SetData(image_array.tobytes())
            bitmap = wx.Bitmap(image)
            dc.DrawBitmap(bitmap, 0, 0)
            
        except Exception as e:
            logMessage(f"[ERROR] Failed to draw elevation: {e}")
            # Fallback to simple display
            dc.SetBackground(wx.Brush(wx.WHITE))
            dc.Clear()
            dc.DrawText(f"Elevation: {self.current_elevation*100:.1f}%", 10, height//2)
    
    def _on_paint_colormap_scale(self, event):
        """Draw the colormap scale indicator using OpenCV."""
        dc = wx.PaintDC(self.colormap_scale_panel)
        width, height = self.colormap_scale_panel.GetSize()
        
        try:
            # Create vertical gradient (0-255)
            gradient = np.linspace(255, 0, height, dtype=np.uint8).reshape(-1, 1)
            gradient = np.tile(gradient, (1, width))
            
            # Apply colormap
            colored = cv2.applyColorMap(gradient, self.colormap_cv2)
            
            # Convert BGR to RGB for wx
            colored_rgb = cv2.cvtColor(colored, cv2.COLOR_BGR2RGB)
            
            # Convert to wx.Bitmap
            image = wx.Image(width, height)
            image.SetData(colored_rgb.tobytes())
            bitmap = wx.Bitmap(image)
            dc.DrawBitmap(bitmap, 0, 0)
            
        except Exception as e:
            logMessage(f"[ERROR] Failed to draw colormap scale: {e}")
    
    def _on_paint_depth_map(self, event):
        """Draw the depth map visualization using OpenCV."""
        dc = wx.PaintDC(self.depth_map_panel)
        width, height = self.depth_map_panel.GetSize()
        
        # Get depth map data
        with self.depth_map_lock:
            depth_map = self.current_depth_map
        
        if depth_map is None or depth_map.size == 0:
            # No data, show message
            dc.SetBackground(wx.Brush(wx.WHITE))
            dc.Clear()
            dc.SetTextForeground(wx.Colour(128, 128, 128))
            font = wx.Font(10, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
            dc.SetFont(font)
            text_width, text_height = dc.GetTextExtent("No depth map data")
            dc.DrawText("No depth map data", (width - text_width) // 2, (height - text_height) // 2)
            return
        
        try:
            # Convert depth map (0-1 float) to grayscale (0-255 uint8)
            depth_gray = (depth_map * 255).astype(np.uint8)
            
            # Apply colormap
            depth_colored = cv2.applyColorMap(depth_gray, self.colormap_cv2)
            
            # Resize to fit panel
            depth_resized = cv2.resize(depth_colored, (width, height), interpolation=cv2.INTER_NEAREST)
            
            # Draw cursor crosshair in center (smaller and thinner)
            cy, cx = height // 2, width // 2
            crosshair_length = 10  # pixels from center
            cv2.line(depth_resized, (cx - crosshair_length, cy), (cx + crosshair_length, cy), (0, 0, 255), 1)
            cv2.line(depth_resized, (cx, cy - crosshair_length), (cx, cy + crosshair_length), (0, 0, 255), 1)
            
            # Convert BGR to RGB for wx
            depth_rgb = cv2.cvtColor(depth_resized, cv2.COLOR_BGR2RGB)
            
            # Convert to wx.Bitmap
            image = wx.Image(width, height)
            image.SetData(depth_rgb.tobytes())
            bitmap = wx.Bitmap(image)
            dc.DrawBitmap(bitmap, 0, 0)
            
        except Exception as e:
            dc.SetBackground(wx.Brush(wx.WHITE))
            dc.Clear()
            dc.SetTextForeground(wx.RED)
            font = wx.Font(9, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
            dc.SetFont(font)
            error_text = f"Error: {str(e)}"
            text_width, text_height = dc.GetTextExtent(error_text)
            dc.DrawText(error_text, (width - text_width) // 2, (height - text_height) // 2)
            logMessage(f"[ERROR] Failed to draw depth map: {e}")
    
    def _on_close(self, event):
        """Handle window close event."""
        self.is_open = False
        if self.timer:
            self.timer.Stop()
            self.timer = None
        if self.frame:
            self.frame.Destroy()
            self.frame = None
