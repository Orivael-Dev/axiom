"""
AXIOM Language Constants — reserved word collision map.

Python keywords that collide with purity validation patterns.
Each maps to a safe Axiom-native synonym.
"""

RESERVED_WORD_COLLISIONS = {
    "class":    "category",   # Python class def
    "type":     "kind",       # Python typing
    "import":   "include",    # Python import
    "return":   "emit",       # Python return
    "global":   "sovereign",  # Python global
    "lambda":   "fn",         # Python lambda
    "yield":    "stream",     # Python yield
    "async":    "concurrent", # Python async
    "with":     "using",      # Python with
    "pass":     "accept",     # Python pass
}
