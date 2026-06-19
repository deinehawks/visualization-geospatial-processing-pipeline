"""
Shared utilities package.

Avoid eager imports here so standalone tools can import lightweight shared
modules without loading optional pipeline dependencies.
"""


def load_pipeline_config(*args, **kwargs):
    from .config import load_pipeline_config as _load_pipeline_config

    return _load_pipeline_config(*args, **kwargs)


__all__ = ["load_pipeline_config"]