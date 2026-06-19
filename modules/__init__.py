"""
Pipeline modules package.

Keep this file lightweight. Do not import stage modules here because standalone
tools such as map.py may run inside QGIS Python, where some pipeline
dependencies may not be installed.
"""

__all__: list[str] = []