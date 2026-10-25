"""Infrastructure layer — concrete adapters for external systems.

This layer implements the ports defined in ``traderlab.domain.ports``.
It is the ONLY layer that may import database drivers, HTTP clients,
file-system utilities, and other external libraries.
"""
