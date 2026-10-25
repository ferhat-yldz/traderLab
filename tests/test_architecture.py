"""Architectural boundary tests.

These tests enforce that the domain layer has NO imports from
infrastructure, markets, interfaces, or agents.

This is a critical architectural invariant — if it breaks, the
Dependency Inversion Principle is violated.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

# Root of the source tree
SRC_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "traderlab"

# Domain layer path
DOMAIN_ROOT = SRC_ROOT / "domain"

# Forbidden import prefixes for the domain layer
FORBIDDEN_PREFIXES = (
    "traderlab.core.config",
    "traderlab.infrastructure",
    "traderlab.markets",
    "traderlab.interfaces",
    "traderlab.agents",
    "traderlab.application",
    "traderlab.research",
    "sqlalchemy",
    "psycopg",
    "alembic",
    "httpx",
    "requests",
    "fastapi",
    "ccxt",
    "openai",
    "pydantic_settings",
)


def _collect_python_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Recursively collect all .py files under the given root."""
    return list(root.rglob("*.py"))


def _extract_imports(filepath: pathlib.Path) -> list[str]:
    """Parse a Python file and extract all imported module names."""
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(filepath))

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


@pytest.mark.smoke
def test_domain_has_no_forbidden_imports() -> None:
    """Verify that domain layer does not import from forbidden packages."""
    violations: list[str] = []
    domain_files = _collect_python_files(DOMAIN_ROOT)

    assert len(domain_files) > 0, "No Python files found in domain layer"

    for filepath in domain_files:
        imports = _extract_imports(filepath)
        for imp in imports:
            for prefix in FORBIDDEN_PREFIXES:
                if imp == prefix or imp.startswith(prefix + "."):
                    rel = filepath.relative_to(SRC_ROOT)
                    violations.append(f"  {rel}: imports '{imp}'")

    if violations:
        msg = "Domain layer has forbidden imports:\n" + "\n".join(violations)
        pytest.fail(msg)


@pytest.mark.smoke
def test_domain_only_imports_core_and_stdlib() -> None:
    """Verify domain imports are limited to stdlib, traderlab.core, and traderlab.domain."""
    allowed_prefixes = ("traderlab.core", "traderlab.domain")
    violations: list[str] = []
    domain_files = _collect_python_files(DOMAIN_ROOT)

    for filepath in domain_files:
        imports = _extract_imports(filepath)
        for imp in imports:
            if imp.startswith("traderlab"):
                is_allowed = any(
                    imp == pfx or imp.startswith(pfx + ".") for pfx in allowed_prefixes
                )
                if not is_allowed:
                    rel = filepath.relative_to(SRC_ROOT)
                    violations.append(f"  {rel}: imports '{imp}'")

    if violations:
        msg = "Domain layer imports non-allowed traderlab packages:\n" + "\n".join(violations)
        pytest.fail(msg)
