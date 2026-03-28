"""
Render pipeline architecture for Touchpoint NVDA addon.
Manages render layers, renderers, and event handlers in a cohesive system.
"""

import inspect

from .utils import logMessage
from .render_layers import (
    RenderLayer,
    SemanticLayer,
    ObjectLayer,
    CaptureRenderer,
    ObjectRenderer,
    DepthRenderer,
    GraphicRenderer,
    ObjectDepthRenderer,
    ElevationRenderer
)
from .effects import ComboEffect, GlobalElevationEffect, VibrationEffect, VibrationIntensityEffect
from .handlers import ObjectHandler, ScreenBorderHandler, ObjectHandlerManager, GlobalHandlerManager
from .filters import GraphicFilter
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
        
        # Get depth label allocation config
        depth_config = self.config.software.get('depth_label_allocation', {})
        max_depth = depth_config.get('max_depth', 5)
        labels_per_depth = depth_config.get('labels_per_depth', 50)
        
        self.capture_layer = RenderLayer(id="capture", dtype=np.uint8, num_channels=3)  # BGR color
        self.object_layer = ObjectLayer(id="object", max_depth=max_depth, labels_per_depth=labels_per_depth)  # Dynamic size follows capture region
        self.depth_layer = RenderLayer(id="depth", dtype=np.float32, constant_size=layer_dims['depth'], num_channels=1)  # Grayscale elevation values
        self.texture_layer = RenderLayer(id="texture", dtype=np.uint8, constant_size=layer_dims['texture'])  # BGR color
        
        # Set plugin reference for all layers
        for layer in self.get_layers():
            layer.set_plugin(self.plugin)
    
    def _create_renderers(self):
        """Create renderers that operate on layers."""
        self.capture_renderer = self._create_renderer_instance(
            "capture_renderer",
            CaptureRenderer,
            self.capture_layer,
        )
        self.object_renderer = self._create_renderer_instance(
            "object_renderer",
            ObjectRenderer,
            self.capture_layer,
            self.object_layer,
        )
        self.depth_renderer = self._create_renderer_instance(
            "depth_renderer",
            DepthRenderer,
            self.capture_layer,
            self.depth_layer,
        )
        self.graphic_renderer = self._create_renderer_instance(
            "graphic_renderer",
            GraphicRenderer,
            self.capture_layer,
            self.depth_layer,
        )
        self.object_depth_renderer = self._create_renderer_instance(
            "object_depth_renderer",
            ObjectDepthRenderer,
            self.object_layer,
            self.depth_layer,
        )
        self.elevation_renderer = self._create_renderer_instance(
            "elevation_renderer",
            ElevationRenderer,
            self.depth_layer,
        )
        
        # Set plugin reference for all renderers
        for renderer in self.get_renderers():
            renderer.set_plugin(self.plugin)

    def _get_renderer_config_kwargs(self, renderer_name):
        """Get kwargs for a renderer from software config.

        Expected config shape:
            software.renderers.<renderer_name>.<property>
        """
        renderers_config = self.config.software.get("renderers", {})
        if not isinstance(renderers_config, dict):
            return {}

        renderer_kwargs = renderers_config.get(renderer_name, {})
        if not isinstance(renderer_kwargs, dict):
            logMessage(f"[RenderPipeline] Ignoring non-dict renderer config for '{renderer_name}'")
            return {}

        return dict(renderer_kwargs)

    def _create_renderer_instance(self, renderer_name, renderer_class, *args):
        """Instantiate renderer_class with filtered kwargs from config."""
        configured_kwargs = self._get_renderer_config_kwargs(renderer_name)
        if not configured_kwargs:
            return renderer_class(*args)

        init_sig = inspect.signature(renderer_class.__init__)
        params = [p for p in init_sig.parameters.values() if p.name != "self"]
        positional_params = [
            p for p in params
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        consumed_positional_names = {p.name for p in positional_params[:len(args)]}

        accepts_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
        valid_names = {p.name for p in params}

        filtered_kwargs = {}
        dropped_keys = []
        for key, value in configured_kwargs.items():
            if key in consumed_positional_names:
                dropped_keys.append(key)
                continue
            if accepts_var_kwargs or key in valid_names:
                filtered_kwargs[key] = value
            else:
                dropped_keys.append(key)

        if dropped_keys:
            logMessage(
                f"[RenderPipeline] Ignored unsupported kwargs for '{renderer_name}': {sorted(dropped_keys)}"
            )

        return renderer_class(*args, **filtered_kwargs)
    
    def _create_handlers(self):
        """Create object and global event handlers."""
        # Object handlers for UI element events
        self.object_handlers = [
            ObjectHandler(filter=GraphicFilter(), effects={
                'enter': ComboEffect([
                    VibrationEffect(effect_ids=[7], priority=1),
                    lambda effect, obj=None, **kwargs: logMessage(f"Mouse entered image: {obj.name if obj and obj.name else 'Unnamed'}")
                ]),
                'leave': ComboEffect([
                    GlobalElevationEffect(0),
                    VibrationEffect(effect_ids=[8], priority=1),
                    lambda effect, obj=None, **kwargs: logMessage(f"Mouse left image: {obj.name if obj and obj.name else 'Unnamed'}")
                ])
            })
        ]
        
        # Global handlers for screen-level events
        self.global_handlers = [
            ScreenBorderHandler(effects={
                'border_enter': ComboEffect([
                    VibrationIntensityEffect(intensity=127, priority=255),
                    lambda effect, obj=None, **kwargs: logMessage("Screen border entered")
                ]),
                'border_leave': ComboEffect([
                    VibrationIntensityEffect(intensity=0, priority=255),
                    lambda effect, obj=None, **kwargs: logMessage("Screen border left")
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
            #self.object_renderer,
            self.graphic_renderer,
            # self.depth_renderer,  # Temporarily disabled
            # self.object_depth_renderer,  # TEMPORARILY DISABLED
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
