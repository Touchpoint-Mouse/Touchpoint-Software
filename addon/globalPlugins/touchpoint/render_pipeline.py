"""
Render pipeline architecture for Touchpoint NVDA addon.
Manages render layers, renderers, and event handlers in a cohesive system.
"""

from .utils import logMessage
from .render_layers import (
    RenderLayer,
    SemanticLayer,
    CaptureRenderer,
    ObjectRenderer,
    DepthRenderer,
    ElevationRenderer
)
from .effects import ComboEffect, GlobalElevationEffect, VibrationEffect
from .handlers import ObjectHandler, ScreenBorderHandler, ObjectHandlerManager, GlobalHandlerManager
from .dependencies import np


class RenderPipeline:
    """Encapsulates the complete render pipeline for haptic feedback.
    
    Manages:
    - Render layers (capture, object, depth, texture)
    - Renderers (capture, object detection, depth calculation, elevation)
    - Object handlers (events for UI elements)
    - Global handlers (screen border detection)
    """
    
    def __init__(self, plugin):
        """Initialize the render pipeline.
        
        Args:
            plugin: The main GlobalPlugin instance with config access
        """
        self.plugin = plugin
        self.config = plugin.config
        
        # Initialize components
        self._create_layers()
        self._create_renderers()
        self._create_handlers()
        self._create_handler_managers()
        
        logMessage("Render pipeline initialized")
    
    def _create_layers(self):
        """Create render layers with computed dimensions from config."""
        layer_dims = self.config.layer_dimensions
        
        self.capture_layer = RenderLayer(id="capture", dtype=np.uint8, num_channels=3)  # BGR color
        self.object_layer = SemanticLayer(id="object", constant_size=None)  # Dynamic size follows capture region
        self.depth_layer = RenderLayer(id="depth", dtype=np.uint8, constant_size=layer_dims['depth'], num_channels=1)  # Grayscale
        self.texture_layer = RenderLayer(id="texture", dtype=np.uint8, constant_size=layer_dims['texture'])  # BGR color
        
        # Set plugin reference for all layers
        for layer in self.get_layers():
            layer.set_plugin(self.plugin)
    
    def _create_renderers(self):
        """Create renderers that operate on layers."""
        self.capture_renderer = CaptureRenderer(self.capture_layer)
        self.object_renderer = ObjectRenderer(self.capture_layer, self.object_layer)
        self.depth_renderer = DepthRenderer(self.capture_layer, self.depth_layer)
        self.elevation_renderer = ElevationRenderer(self.depth_layer)
        
        # Set plugin reference for all renderers
        for renderer in self.get_renderers():
            renderer.set_plugin(self.plugin)
    
    def _create_handlers(self):
        """Create object and global event handlers."""
        # Object handlers for UI element events
        self.object_handlers = [
            ObjectHandler(effects={
                'enter': ComboEffect([
                    VibrationEffect(0.1, 180.0, 1),
                    lambda effect, obj=None, **kwargs: logMessage(f"Mouse entered: {obj.name if obj and obj.name else 'Unnamed'}")
                ]),
                'leave': ComboEffect([
                    GlobalElevationEffect(0),
                    VibrationEffect(0.05, 80.0, 1),
                    lambda effect, obj=None, **kwargs: logMessage(f"Mouse left: {obj.name if obj and obj.name else 'Unnamed'}")
                ])
            })
        ]
        
        # Global handlers for screen-level events
        self.global_handlers = [
            ScreenBorderHandler(effects={
                'border_enter': ComboEffect([
                    VibrationEffect(0.1, 200.0, 0),
                    lambda effect, **kwargs: logMessage("Screen border entered")
                ]),
                'border_leave': ComboEffect([
                    VibrationEffect(0, 0, 0),
                    lambda effect, **kwargs: logMessage("Screen border left")
                ])
            })
        ]
    
    def _create_handler_managers(self):
        """Create and populate handler managers."""
        # Object handler manager for UI element events
        self.object_handler_manager = ObjectHandlerManager(self.plugin)
        self.object_handler_manager.populate(self.object_handlers)
        
        # Global handler manager for screen-level events
        self.global_handler_manager = GlobalHandlerManager(self.plugin)
        self.global_handler_manager.populate(self.global_handlers)
    
    def initialize_renderers(self):
        """Initialize all renderers (called after plugin is fully set up)."""
        for renderer in self.get_renderers():
            renderer.initialize()
    
    def get_layer_ids(self):
        """Get list of all layer IDs.
        
        Returns:
            list: Layer ID strings
        """
        return [layer.id for layer in self.get_layers()]
    
    def get_layers(self):
        """Get all render layers as a list.
        
        Returns:
            list: All RenderLayer instances in order
        """
        return [
            self.capture_layer,
            self.object_layer,
            self.depth_layer,
            self.texture_layer
        ]
    
    def get_renderers(self):
        """Get all renderers as a list.
        
        Returns:
            list: All Renderer instances in execution order
        """
        return [
            self.capture_renderer,
            self.object_renderer,
            self.depth_renderer,
            self.elevation_renderer
        ]
    
    def get_object_handlers(self):
        """Get object event handlers.
        
        Returns:
            list: ObjectHandler instances
        """
        return self.object_handlers
    
    def get_global_handlers(self):
        """Get global event handlers.
        
        Returns:
            list: GlobalHandler instances
        """
        return self.global_handlers
    
    def execute_render_cycle(self):
        """Execute a complete render cycle through all renderers.
        
        Assumes region bounds have already been updated by caller.
        """
        for renderer in self.get_renderers():
            renderer()
    
    def update_layer_regions(self, new_region):
        """Update region bounds for all layers.
        
        Args:
            new_region: Region namedtuple with (left, top, width, height)
        """
        for layer in self.get_layers():
            layer.update_region_bounds(new_region)
    
    def cycle_layer_states(self):
        """Cycle all layers to prepare for next frame."""
        for layer in self.get_layers():
            layer.cycle_state()
    
    def dispatch_handlers(self):
        """Dispatch global handler events."""
        self.global_handler_manager.dispatch_events()
