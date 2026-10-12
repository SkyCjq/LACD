"""Bundled LACD templates (managed files + manifest.json).

This package exists so ``lacd.cli.templates_dir()`` can locate templates via
``importlib.resources`` from a wheel, an editable install, or a source tree
(MODULE_CONTRACT §9, D1).
"""
