# -*- coding: utf-8 -*-
"""Centralized external dependencies management for Touchpoint addon."""

import sys
import os

# Setup dependency path
module_path = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, module_path)

import dependency_checker

# Add parent directory to path to import songbird
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

# Expand dependencies path if available
dependency_checker.expand_path()

# Import and expose all external dependencies
try:
    import numpy as np
    import cv2
    import songbird
    
    DEPENDENCIES_AVAILABLE = True
    IMPORT_ERROR = None
except ImportError as e:
    # Provide None fallbacks for type checking
    np = None
    cv2 = None
    songbird = None
    
    DEPENDENCIES_AVAILABLE = False
    IMPORT_ERROR = str(e)

# Export all dependencies
__all__ = ['np', 'cv2', 'songbird', 'DEPENDENCIES_AVAILABLE', 'IMPORT_ERROR', 'dependency_checker']
