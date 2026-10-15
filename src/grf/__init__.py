"""General Robot Framework: the generic base of product test automation frameworks with Robot Framework.

A project is a folder with a ``grf.toml`` (paths, tiers, capabilities, rig, lint rules, texts) and an optional
product plugin (``grf.plugin.ProductPlugin``); see ``grf.project``.
"""

from importlib.metadata import version

__version__ = version("general-robot-framework")
