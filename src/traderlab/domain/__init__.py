"""Domain layer — pure business logic.

This package and all its sub-packages must have ZERO dependencies on
infrastructure (SQLAlchemy, psycopg, HTTP clients, file-system I/O, etc.).

Allowed imports:
    - Python standard library
    - ``traderlab.core`` (types, errors)
    - Other ``traderlab.domain`` sub-packages
"""
