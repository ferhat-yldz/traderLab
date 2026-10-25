"""Application layer — use cases and orchestration.

This layer sits between interfaces and domain.  It:
    - Coordinates domain logic and infrastructure ports
    - Implements use-case specific workflows (ingestion, analysis)
    - Does NOT contain business rules (those belong in domain)
"""
