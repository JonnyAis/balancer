"""
Shim module so notebooks can import EfficientDataManager without referencing the simulations package path.
Usage:
    from efficient_data_manager import EfficientDataManager
This simply re-exports the class from simulations.efficient_data_manager.
"""
from simulations.efficient_data_manager import EfficientDataManager

__all__ = ["EfficientDataManager"]
