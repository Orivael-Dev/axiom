#!/usr/bin/env python3
"""Issue a fresh recovery code for an existing tenant.

Use this:
  - To bootstrap a recovery code for accounts created before the
    password-reset feature shipped (recovery_hash = NULL in the DB).
  - As an ops escape hatch when a customer has lost both their
    password AND their recovery code: verify their identity out of
    band, then re-issue a code with this script.

The plaintext recovery code is printed ONCE to stdout. Capture it.
We only persist its PBKDF2 hash — there is no second chance to read it.

Run from the box, in the same working directory as your tenants/ tree
(or set AXIOM_FIREWALL_TENANT_DIR + AXIOM_MASTER_KEY first):

    export AXIOM_MASTER_KEY=...                     # same as the running server
    export AXIOM_FIREWALL_TENANT_DIR=/data/tenants  # if not in cwd
    python3 scripts/issue_recovery_code.py --email me@example.com
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--email", required=True, help="Account email")
    args = ap.parse_args()

    if not os.environ.get("AXIOM_MASTER_KEY"):
        sys.exit(
            "AXIOM_MASTER_KEY must be set to the same value the server uses, "
            "otherwise the hash you write will not match the one the server "
            "verifies against."
        )

    from axiom_firewall.auth import generate_recovery_code, hash_password
    from axiom_firewall.db import (
        find_tenant_by_email, update_tenant_recovery_hash,
    )

    tenant = find_tenant_by_email(args.email)
    if not tenant:
        sys.exit(f"No tenant found with email {args.email!r}")

    code = generate_recovery_code()
    update_tenant_recovery_hash(
        tenant.tenant_id, recovery_hash=hash_password(code),
    )

    print()
    print(f"  Tenant:        {tenant.email}")
    print(f"  Tenant ID:     {tenant.tenant_id}")
    print(f"  Recovery code: {code}")
    print()
    print("  Save this code now — it will not be shown again.")
    print("  Old recovery code (if any) is now invalid.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
