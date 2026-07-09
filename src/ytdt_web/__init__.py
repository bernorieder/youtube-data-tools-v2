"""Web frontend for YouTube Data Tools.

Two layers, kept strictly apart so the UI framework can be swapped:

- :mod:`ytdt_web.jobs` — framework-agnostic: module metadata, parameter
  handling, background execution with progress state, output files.
- :mod:`ytdt_web.app` — the NiceGUI presentation layer; the only module
  that imports the framework.
"""
