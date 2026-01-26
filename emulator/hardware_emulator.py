"""
Touchpoint Hardware Emulator

A standalone tkinter application that emulates the Touchpoint hardware device.
Communicates with the NVDA addon via UDP using the Songbird protocol.
"""

import sys
import os
import time
import threading
import tkinter as tk
from tkinter import ttk

# Add deps to path for songbird
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'deps', 'touchpoint-deps-py311'))

from songbird import SongbirdUDP


class TouchpointEmulator:
    """Main emulator class."""
    
    # Header definitions (must match hardware_driver.py)
    H_PING = 0xFF
    H_ELEVATION = 0x10
    H_ELEVATION_SPEED = 0x11
    H_VIBRATION = 0x20
    H_STATUS = 0x30
    
    # UDP configuration
    UDP_ADDON_PORT = 7420  # Port addon listens on
    UDP_EMULATOR_PORT = 7421  # Port emulator listens on
    UDP_LOCALHOST = "127.0.0.1"
    
    def __init__(self, root):
        """Initialize the emulator."""
        self.root = root
        self.root.title("Touchpoint Hardware Emulator")
        self.root.geometry("600x900")
        self.root.resizable(False, False)
        
        # State variables
        self.addon_connected = False
        self.hardware_connected = False
        self.current_elevation = 0.0
        self.target_elevation = 0.0
        self.max_elevation_speed = 1.0  # units per second (1 unit = full range)
        self.last_update_time = time.time()
    
        # UDP components
        self.udp = None
        self.udp_core = None
        self.udp_running = False
        self.ping_thread = None
        
        # Build GUI
        self._build_gui()
        
        # Initialize UDP
        self._initialize_udp()
        
        # Start periodic updates
        self._update_display()
        
        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _build_gui(self):
        """Build the GUI interface."""
        # Main container
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title
        title_label = ttk.Label(main_frame, text="Touchpoint Hardware Emulator", 
                               font=("Arial", 16, "bold"))
        title_label.pack(pady=(0, 20))
        
        # Connection Status Section
        status_frame = ttk.LabelFrame(main_frame, text="Connection Status", padding="10")
        status_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Addon connection status
        addon_frame = ttk.Frame(status_frame)
        addon_frame.pack(fill=tk.X, pady=5)
        ttk.Label(addon_frame, text="NVDA Addon:", width=20).pack(side=tk.LEFT)
        self.addon_status_label = ttk.Label(addon_frame, text="Disconnected", 
                                           foreground="red", font=("Arial", 10, "bold"))
        self.addon_status_label.pack(side=tk.LEFT)
        
        # Hardware connection status
        hardware_frame = ttk.Frame(status_frame)
        hardware_frame.pack(fill=tk.X, pady=5)
        ttk.Label(hardware_frame, text="Physical Hardware:", width=20).pack(side=tk.LEFT)
        self.hardware_status_label = ttk.Label(hardware_frame, text="Disconnected", 
                                              foreground="red", font=("Arial", 10, "bold"))
        self.hardware_status_label.pack(side=tk.LEFT)
        
        # Elevation Indicator Section
        elevation_frame = ttk.LabelFrame(main_frame, text="Elevation Level", padding="10")
        elevation_frame.pack(fill=tk.BOTH, pady=(0, 10))
        
        # Elevation canvas (water level style)
        elevation_container = ttk.Frame(elevation_frame)
        elevation_container.pack(fill=tk.X)
        
        self.elevation_canvas = tk.Canvas(elevation_container, width=560, height=200, 
                                         bg="white", relief=tk.SUNKEN, borderwidth=2)
        self.elevation_canvas.pack(pady=(0, 5))
        
        # Elevation value label
        self.elevation_value_label = ttk.Label(elevation_frame, text="0.0%", 
                                              font=("Arial", 14, "bold"))
        self.elevation_value_label.pack()
        
        # Vibration Indicator Section
        vibration_frame = ttk.LabelFrame(main_frame, text="Vibration Events", padding="10")
        vibration_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Vibration events log (scrollable)
        log_frame = ttk.Frame(vibration_frame)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
        
        # Create scrollable text widget
        log_container = ttk.Frame(log_frame)
        log_container.pack(fill=tk.BOTH, expand=True)
        
        self.vibration_log = tk.Text(log_container, height=6, width=60, 
                                     font=("Courier", 9), wrap=tk.NONE,
                                     bg="#f5f5f5", fg="#333333")
        self.vibration_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Add scrollbar
        log_scrollbar = ttk.Scrollbar(log_container, orient=tk.VERTICAL, 
                                     command=self.vibration_log.yview)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.vibration_log.config(yscrollcommand=log_scrollbar.set)
        
        # Make text widget read-only
        self.vibration_log.config(state=tk.DISABLED)
        
        # UDP Info
        udp_frame = ttk.Frame(main_frame)
        udp_frame.pack(fill=tk.X, pady=(10, 0))
        udp_label = ttk.Label(udp_frame, text=f"Listening on UDP port {self.UDP_EMULATOR_PORT}", 
                             font=("Arial", 8), foreground="gray")
        udp_label.pack()
    
    def _initialize_udp(self):
        """Initialize UDP communication."""
        try:
            self.udp = SongbirdUDP("Touchpoint Emulator")
            self.udp_core = self.udp.get_protocol()
            self.udp_core.set_allow_out_of_order(False)
            
            # Register handlers for receiving commands
            self.udp_core.set_header_handler(self.H_PING, self._handle_ping)
            self.udp_core.set_header_handler(self.H_ELEVATION, self._handle_elevation)
            self.udp_core.set_header_handler(self.H_ELEVATION_SPEED, self._handle_elevation_speed)
            self.udp_core.set_header_handler(self.H_VIBRATION, self._handle_vibration)
            self.udp_core.set_header_handler(self.H_STATUS, self._handle_status)
            
            # Listen on emulator port
            if self.udp.listen(self.UDP_EMULATOR_PORT):
                # Set default remote to addon port
                self.udp.set_remote(self.UDP_LOCALHOST, self.UDP_ADDON_PORT)
                self.udp_running = True
                print(f"UDP listener started on port {self.UDP_EMULATOR_PORT}")
                
                # Start ping thread
                self.ping_thread = threading.Thread(target=self._ping_loop, daemon=True)
                self.ping_thread.start()
            else:
                print("Failed to start UDP listener")
        except Exception as e:
            print(f"UDP initialization error: {e}")
    
    def _ping_loop(self):
        """Continuously ping the addon to establish/maintain connection."""
        while self.udp_running:
            try:
                if not self.addon_connected:
                    # Send ping to addon
                    pkt = self.udp_core.create_packet(self.H_PING)
                    self.udp_core.send_packet(pkt)
                
                # Ping every 2 seconds when disconnected, every 5 seconds when connected
                time.sleep(2 if not self.addon_connected else 5)
            except Exception as e:
                print(f"Ping error: {e}")
                time.sleep(2)
    
    def _handle_ping(self, packet):
        """Handle ping response from addon."""
        if not self.addon_connected:
            self.addon_connected = True
            print("Connected to NVDA addon")
            self._update_connection_status()
    
    def _handle_elevation(self, packet):
        """Handle elevation command from addon."""
        try:
            elevation = packet.read_float()
            self.target_elevation = elevation
            print(f"Received target elevation: {self.target_elevation}")
        except Exception as e:
            print(f"Error handling elevation: {e}")
    
    def _handle_elevation_speed(self, packet):
        """Handle elevation speed command from addon."""
        try:
            speed = packet.read_float()
            self.max_elevation_speed = speed
            print(f"Received max elevation speed: {self.max_elevation_speed} units/sec")
        except Exception as e:
            print(f"Error handling elevation speed: {e}")
    
    def _handle_vibration(self, packet):
        """Handle vibration command from addon."""
        try:
            amplitude = packet.read_float()
            frequency = packet.read_float()
            duration = packet.read_int16()
            
            # Add to event log
            current_time = time.time()
            timestamp = time.strftime("%H:%M:%S", time.localtime(current_time))
            milliseconds = int((current_time % 1) * 1000)
            timestamp_with_ms = f"{timestamp}.{milliseconds:03d}"
            dur_str = "indefinite" if duration == 0 else f"{duration}pulses"
            if (amplitude == 0.0 or frequency == 0.0):
                log_entry = f"[{timestamp_with_ms}] Vibration stopped\n"
            else:
                log_entry = f"[{timestamp_with_ms}] Amp: {amplitude:.3f}  Freq: {frequency:6.1f}Hz  Dur: {dur_str}\n"
            
            self.vibration_log.config(state=tk.NORMAL)
            self.vibration_log.insert(tk.END, log_entry)
            # Limit log to last 100 lines
            lines = int(self.vibration_log.index('end-1c').split('.')[0])
            if lines > 100:
                self.vibration_log.delete('1.0', '2.0')
            self.vibration_log.see(tk.END)  # Auto-scroll to bottom
            self.vibration_log.config(state=tk.DISABLED)
            
            print(log_entry.strip())
        except Exception as e:
            print(f"Error handling vibration: {e}")
    
    def _handle_status(self, packet):
        """Handle status update from addon."""
        try:
            hardware_status = packet.read_uint8()
            self.hardware_connected = (hardware_status == 1)
            print(f"Hardware status: {'Connected' if self.hardware_connected else 'Not Connected'}")
            self._update_connection_status()
        except Exception as e:
            print(f"Error handling status: {e}")
    
    def _update_connection_status(self):
        """Update the connection status labels."""
        # Update addon status
        if self.addon_connected:
            self.addon_status_label.config(text="Connected", foreground="green")
        else:
            self.addon_status_label.config(text="Disconnected", foreground="red")
        
        # Update hardware status
        if self.hardware_connected:
            self.hardware_status_label.config(text="Connected", foreground="green")
        else:
            self.hardware_status_label.config(text="Disconnected", foreground="red")
    
    def _update_display(self):
        """Periodic update of the display elements."""
        current_time = time.time()
        delta_time = current_time - self.last_update_time
        self.last_update_time = current_time
        
        # Smoothly animate elevation towards target based on speed
        if abs(self.target_elevation - self.current_elevation) > 0.01:
            # Calculate max change for this frame (speed is in units/second, where 1 unit = 100%)
            max_change = self.max_elevation_speed * delta_time
            
            # Move towards target
            diff = self.target_elevation - self.current_elevation
            if abs(diff) <= max_change:
                self.current_elevation = self.target_elevation
            else:
                self.current_elevation += max_change if diff > 0 else -max_change
        
        # Update elevation display
        self._draw_elevation()
        
        # Schedule next update (60 FPS)
        self.root.after(16, self._update_display)
    
    def _draw_elevation(self):
        """Draw the elevation water level indicator."""
        canvas = self.elevation_canvas
        width = canvas.winfo_width() if canvas.winfo_width() > 1 else 560
        height = canvas.winfo_height() if canvas.winfo_height() > 1 else 200
        
        # Clear canvas
        canvas.delete("all")
        
        # Draw border
        canvas.create_rectangle(2, 2, width-2, height-2, outline="black", width=2)
        
        # Calculate water level height
        water_height = (self.current_elevation) * (height - 4)
        
        # Draw water level (blue gradient)
        if water_height > 0:
            water_y = height - 2 - water_height
            # Main water body
            canvas.create_rectangle(2, water_y, width-2, height-2, 
                                   fill="#4da6ff", outline="")
        
        # Draw percentage markers (with padding to prevent cutoff)
        for i in range(0, 11):
            y = height - 2 - (i * 0.1 * (height - 4))
            # Clamp y to ensure text doesn't get cut off (text needs ~10px padding)
            y_clamped = max(10, min(height - 10, y))
            canvas.create_line(5, y, 15, y, fill="gray", width=1)
            canvas.create_text(width - 35, y_clamped, text=f"{i*10}%", font=("Arial", 8), anchor="e")
        
        # Update value label
        self.elevation_value_label.config(text=f"{self.current_elevation*100:.1f}%")
    
    def _on_close(self):
        """Handle window close event."""
        print("Closing emulator...")
        self.udp_running = False
        if self.udp:
            self.udp.close()
        self.root.destroy()


def main():
    """Main entry point."""
    root = tk.Tk()
    app = TouchpointEmulator(root)
    root.mainloop()


if __name__ == "__main__":
    main()
