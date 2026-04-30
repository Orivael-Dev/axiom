"""
AXIOM — An AI-Native Language for Building Self-Evolving Intelligence
Phase 1: Self-Improving Prompt Agent
"""

__version__ = "1.8.7"

# Core exports (always available)
from axiom_files.validator import validate
from axiom_files.parser    import load_axiom, save_axiom

# Guard exports (available when installed with [guard])
try:
    from axiom_constitutional.guards.axiom_destructive_guard import DestructiveOperationGuard
    from axiom_constitutional.guards.axiom_pii_guard import PIIGuard
    from axiom_constitutional.guards.axiom_injection_guard import OutputInjectionGuard
    from axiom_constitutional.guards.axiom_agency_guard import AgencyGuard
    from axiom_constitutional.guards.axiom_review_queue import ReviewQueue
    GUARD_AVAILABLE = True
except ImportError:
    GUARD_AVAILABLE = False
