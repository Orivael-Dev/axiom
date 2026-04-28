#!/usr/bin/env bash
# Install AXIOM git hooks into .git/hooks/
# Run once after cloning: bash scripts/install_hooks.sh

set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

cat > "$HOOKS_DIR/pre-push" << 'HOOK'
#!/usr/bin/env bash
# AXIOM Pre-Push Hook — blocks push if guards or agents fail certification.
# Bypass (emergency only): git push --no-verify

set -e
REPO_ROOT="$(git rev-parse --show-toplevel)"
PREFLIGHT="$REPO_ROOT/scripts/axiom_preflight.py"

if [ -f "$REPO_ROOT/venv/Scripts/activate" ]; then
    source "$REPO_ROOT/venv/Scripts/activate"
elif [ -f "$REPO_ROOT/venv/bin/activate" ]; then
    source "$REPO_ROOT/venv/bin/activate"
fi

python "$PREFLIGHT" --base origin/main
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "  Push blocked by AXIOM preflight."
    echo "  Run: python scripts/axiom_preflight.py --all  for full report."
    echo "  Use --no-verify only if you accept full responsibility."
    echo ""
    exit 1
fi
exit 0
HOOK

chmod +x "$HOOKS_DIR/pre-push"
echo "Installed: .git/hooks/pre-push"
echo "Test with: python scripts/axiom_preflight.py --base HEAD~1"
