"""
Centralized configuration manager for Touchpoint NVDA addon.
Provides singleton access to hardware and software configurations with cached computed values.
"""

import json
import os
import math
from .utils import logMessage


class TouchpointConfig:
    """Singleton configuration manager for Touchpoint addon.
    
    Loads hardware and software configurations once and provides cached access
    to both raw config values and computed derived values like layer dimensions.
    """
    
    _instance = None
    
    def __init__(self):
        """Initialize configuration (use get_instance() instead of direct instantiation)."""
        # Load raw configurations
        self.hardware = self._load_hardware_config()
        self.software = self._load_software_config()
        
        # Validate configurations
        self._validate_config()
        
        # Compute derived values (cached for performance)
        self.layer_dimensions = self._calculate_layer_dimensions()
        self.hardware_dimensions = self._calculate_hardware_dimensions()  # Hardware area without padding
        self.capture_dimensions = self._calculate_capture_dimensions()  # Capture area with padding
        self.capture_padding = self.software['capture_region'].get('padding', 0)
        
        # Log configuration summary
        self._log_configuration()
    
    @classmethod
    def get_instance(cls):
        """Get the singleton configuration instance.
        
        Returns:
            TouchpointConfig: The singleton configuration instance
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        """Reset the singleton instance (useful for testing or reloading config)."""
        cls._instance = None
    
    def _load_hardware_config(self):
        """Load hardware configuration from JSON file.
        
        Returns:
            dict: Hardware configuration dictionary
        """
        config_path = os.path.join(os.path.dirname(__file__), 'hardware_config.json')
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logMessage(f"[ERROR] Failed to load hardware config: {e}")
            # Return default configuration
            return {
                "headers": {
                    "ping": 255,
                    "elevation": 16,
                    "elevation_speed": 17,
                    "vibration_effect": 32,
                    "vibration_intensity": 33
                },
                "serial": {"port": "COM6", "baud_rate": 460800},
                "display": {"resolution": 36.0, "aspect_ratio": 0.5},
                "elevation": {"max_elevation": 180, "max_elevation_speed": 180},
                "vibration": {"max_intensity": 127}
            }
    
    def _load_software_config(self):
        """Load software configuration from JSON file.
        
        Returns:
            dict: Software configuration dictionary
        """
        config_path = os.path.join(os.path.dirname(__file__), 'software_config.json')
        try:
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logMessage(f"[ERROR] Failed to load software config: {e}")
            # Return defaults
            return {
                "capture_region": {"area": 10000, "aspect_ratio": 1.0},
                "layer_multipliers": {"depth": 4.0, "texture": 1.0},
                "threading": {"capture": 0.01, "render": 0.01}
            }
    
    def _validate_config(self):
        """Validate configuration values to catch errors early."""
        try:
            # Validate hardware config
            assert self.hardware['display']['resolution'] > 0, "Hardware resolution must be positive"
            assert self.hardware['display']['aspect_ratio'] > 0, "Hardware aspect ratio must be positive"
            assert self.hardware['elevation']['max_elevation'] > 0, "Max elevation must be positive"
            assert self.hardware['elevation']['max_elevation_speed'] > 0, "Max elevation speed must be positive"
            
            # Validate software config
            assert self.software['capture_region']['area'] > 0, "Capture area must be positive"
            assert self.software['capture_region']['aspect_ratio'] > 0, "Capture aspect ratio must be positive"
            
            for layer_name, multiplier in self.software['layer_multipliers'].items():
                assert multiplier > 0, f"Layer multiplier for '{layer_name}' must be positive"
                
            for layer_name, multiplier in self.software['threading'].items():
                assert multiplier > 0, f"Thread delay for '{layer_name}' must be positive"
                
        except AssertionError as e:
            logMessage(f"[ERROR] Configuration validation failed: {e}")
            raise
    
    def _calculate_layer_dimensions(self):
        """Calculate layer dimensions based on hardware and software configuration.
        
        Returns:
            dict: Dictionary mapping layer names to (width, height) tuples
        """
        hw_resolution = self.hardware['display']['resolution']
        hw_aspect_ratio = self.hardware['display']['aspect_ratio']
        
        multipliers = self.software['layer_multipliers']
        
        dimensions = {}
        for layer_name, multiplier in multipliers.items():
            # Calculate area for this layer
            layer_area = hw_resolution * multiplier
            
            # Calculate dimensions using hardware aspect ratio
            # area = width * height, aspect_ratio = width / height
            # height = sqrt(area / aspect_ratio), width = height * aspect_ratio  
            height = int(math.sqrt(layer_area / hw_aspect_ratio))
            width = int(height * hw_aspect_ratio)
            
            dimensions[layer_name] = (width, height)
        
        return dimensions
    
    def _calculate_hardware_dimensions(self):
        """Calculate hardware area dimensions (without padding) from area and hardware aspect ratio.
        
        Returns:
            tuple: (width, height) in pixels
        """
        capture_area = self.software['capture_region']['area']
        hardware_aspect_ratio = self.hardware['display']['aspect_ratio']
        
        # area = width * height, aspect_ratio = width / height
        # height = sqrt(area / aspect_ratio), width = height * aspect_ratio
        hardware_height = int(math.sqrt(capture_area / hardware_aspect_ratio))
        hardware_width = int(hardware_height * hardware_aspect_ratio)
        
        return (hardware_width, hardware_height)
    
    def _calculate_capture_dimensions(self):
        """Calculate capture region dimensions with padding from area and aspect ratio.
        
        Returns:
            tuple: (width, height) in pixels (includes padding on all sides)
        """
        hardware_width, hardware_height = self.hardware_dimensions
        padding = self.software['capture_region'].get('padding', 0)
        
        # Add padding on all sides (left, right, top, bottom)
        capture_width = hardware_width + (2 * padding)
        capture_height = hardware_height + (2 * padding)
        
        return (capture_width, capture_height)
    
    def _log_configuration(self):
        """Log configuration summary for debugging."""
        hw_width, hw_height = self.hardware_dimensions
        cap_width, cap_height = self.capture_dimensions
        padding = self.capture_padding
        logMessage(f"Configuration loaded:")
        logMessage(f"  Hardware area: {hw_width}x{hw_height} pixels (area={self.software['capture_region']['area']}, aspect={self.hardware['display']['aspect_ratio']})")
        logMessage(f"  Capture region: {cap_width}x{cap_height} pixels (with {padding}px padding on all sides)")
        
        for layer_name, (w, h) in self.layer_dimensions.items():
            area = w * h
            multiplier = self.software['layer_multipliers'][layer_name]
            logMessage(f"  Layer '{layer_name}': {w}x{h} pixels (area={area}, multiplier={multiplier})")
    
    def get_mesh_dimensions(self):
        """Calculate mesh dimensions from hardware display resolution and aspect ratio.
        
        Returns:
            tuple: (mesh_height, mesh_width) in texture points
        """
        hw_resolution = self.hardware['display']['resolution']
        hw_aspect_ratio = self.hardware['display']['aspect_ratio']
        
        mesh_height = int(math.sqrt(hw_resolution / hw_aspect_ratio))
        mesh_width = int(mesh_height * hw_aspect_ratio)
        
        return (mesh_height, mesh_width)
