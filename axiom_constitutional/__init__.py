"""
AXIOM — An AI-Native Language for Building Self-Evolving Intelligence
Phase 1: Self-Improving Prompt Agent
"""

__version__ = "1.8.6"

# Core exports (always available)
from axiom_constitutional.validator import validate
from axiom_constitutional.certifier import certify
from axiom_constitutional.runner    import run
from axiom_constitutional.manifest  import generate_manifest

# Guard exports (available when installed with [guard])
try:
    from axiom_constitutional.axiom_guard_api import app as guard_app
    from axiom_constitutional.axiom_destructive_guard import DestructiveOperationGuard
    from axiom_constitutional.axiom_pii_guard import PIIGuard
    from axiom_constitutional.axiom_injection_guard import OutputInjectionGuard
    from axiom_constitutional.axiom_agency_guard import AgencyGuard
    from axiom_constitutional.axiom_review_queue import ReviewQueue
    GUARD_AVAILABLE = True
except ImportError:
    GUARD_AVAILABLE = False
