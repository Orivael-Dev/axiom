# Skill Packs

A Skill Pack is a single-file JSON manifest that bundles a Firewall
policy with metadata (name, version, author, license). Packs are
**signed**, **versioned**, and **shareable** — install one to get a
curated set of block patterns for a specific use case without writing
regex yourself.

## Five first-party packs

| Name | What it blocks |
|---|---|
| `customer-support-base` | Impersonation, refund-fraud language, prompt-injection |
| `code-review-base` | "Add a backdoor" / hardcoded secrets / disable auth requests |
| `fdcpa` | US Fair Debt Collection Practices Act violations — arrest threats, false legal authority, time-of-day violations |
| `hipaa-intake` | PHI-leakage patterns in patient-intake chatbots |
| `gdpr-article-9` | Elicitation of GDPR special-category data without explicit consent |

## Install one

**Via dashboard:** Dashboard → **Packs** → click **Install** on the one you want.

**Via CLI** (recommended for CI / Sovereign-Box / scripted setups):

```bash
# List what the registry offers
axiom-packs list

# Install a pack into ./packs/<name>/pack.json (downloads + verifies)
axiom-packs install fdcpa

# Or into a specific directory + a specific version
axiom-packs install fdcpa --version 0.1.0 --dest /etc/axiom/packs

# Show a pack's manifest without installing
axiom-packs show coppa

# Locally verify a pack you've already got
axiom-packs verify /etc/axiom/packs/fdcpa/pack.json

# Where am I pointing?
axiom-packs sources
```

The CLI is `axiom_packs_cli.py` at the repo root — alias it as
`axiom-packs` for convenience:

```bash
alias axiom-packs='python3 /path/to/axiom/axiom_packs_cli.py'
```

Registry resolution priority:

1. `--registry <url>` flag
2. `AXIOM_PACKS_REGISTRY` env var
3. `http://localhost:8002` (the default when self-hosting the registry image)

Public Orivael-Dev registry:

```bash
export AXIOM_PACKS_REGISTRY=https://packs.orivael.dev
```

Every install verifies the pack's HMAC signature under the
`axiom-skill-pack-first-party-v1` namespace **before** writing
anything to disk. A pack that fails verification is never
installed; the CLI exits non-zero.

Once a pack is on disk, the firewall picks it up the same way it
does for dashboard installs — the install path is identical, only
the trigger differs.

## What's in a pack

```json
{
  "format_version": "1.0",
  "name": "fdcpa",
  "title": "FDCPA — Fair Debt Collection Practices Act",
  "description": "Block US-FDCPA-prohibited debt-collection patterns...",
  "version": "0.1.0",
  "author": "Orivael Dev",
  "license": "MIT",
  "homepage": "https://docs.orivael.dev/firewall/packs/fdcpa",
  "tags": ["compliance", "finance", "us-federal"],
  "tested_against": ["axiom-firewall>=0.1.0"],
  "policy": {
    "version": 1,
    "additional_block_patterns": [
      {"class": "HARM", "regex": "warrant\\s+for\\s+(?:your|the\\s+debtor's)\\s+arrest"}
    ],
    "disabled_default_classes": [],
    "allow_only_classes": null
  },
  "signature": "808f53b24ac8acdf513148a95a019a10578803dcb8f057d6be7a155ec0ef17dc"
}
```

| Field | Required | Description |
|---|---|---|
| `format_version` | yes | Always `"1.0"` for this release. [2-year backward-compat](PHASE_1_DECISIONS.md). |
| `name` | yes | Unique kebab-case slug. `^[a-z][a-z0-9-]{1,63}$`. |
| `title` | yes | Human-readable name shown in the dashboard. |
| `description` | yes | One paragraph — what this pack is for. |
| `version` | yes | Semver (`0.1.0`, `1.0.0-rc.1`). |
| `author` | yes | Publisher name. |
| `license` | yes | SPDX identifier (`MIT`, `Apache-2.0`, etc.). |
| `homepage` | no | URL to the pack's docs page. |
| `tags` | no | Free-form labels for filtering. |
| `tested_against` | no | Compatibility hints (`axiom-firewall>=0.1.0`). |
| `policy` | yes | The actual policy. Same schema as the [custom policy](custom-policies.md). |
| `signature` | yes (after signing) | HMAC-SHA256 of the canonical payload minus this field. |

## Signing

First-party packs (published by Orivael) are signed with a key
derived from `AXIOM_MASTER_KEY` under the namespace
`axiom-skill-pack-v1`. The dashboard REFUSES to install a pack with
an invalid or missing signature.

To re-sign after editing:

```bash
AXIOM_MASTER_KEY=<hex> python scripts/sign_packs.py packs/fdcpa
```

The script is idempotent — unchanged packs are left alone.

Third-party publishing keys come in Phase 2 week 6 alongside the
public registry at `packs.orivael.dev`.

## Customizing an installed pack

A pack installs by writing its policy section into the tenant
policy table. Two consequences:

1. The **Policy editor** at `/dashboard/policy` shows the pack's JSON.
   You can edit it freely — the pack's lineage is still tracked
   separately so the dashboard can show "based on `fdcpa@0.1.0`".

2. Uninstalling a pack also clears the policy. If you'd customized
   it, those edits are lost. Save your edits elsewhere first if you
   want them.

## Authoring your own pack

```bash
# Skeleton: a directory named after the pack with a pack.json inside.
mkdir packs/my-org-internal/
cp packs/customer-support-base/pack.json packs/my-org-internal/pack.json

# Edit metadata + policy
$EDITOR packs/my-org-internal/pack.json

# Sign with your master key
AXIOM_MASTER_KEY=<hex> python scripts/sign_packs.py packs/my-org-internal

# Restart the dashboard — your pack shows up alongside the first-party ones.
```

For self-hosters, point `AXIOM_FIREWALL_PACKS_DIR` at any directory
containing `<pack-name>/pack.json` files.

## What's a good pack pattern?

- **High precision over recall.** A pack with one false positive a
  week destroys user trust. A pack with one false negative a month
  is fine — the default classifier covers a lot.
- **Case-insensitive matching.** All regex compile with `re.IGNORECASE`.
- **Test against allowed text too.** Make sure innocuous mentions of
  the keyword don't trip the block. `(?:tell|ask)\s+me\s+about\s+your\s+religion`
  is fine; `religion` alone would block legitimate questions like
  "what's the most common world religion?".
- **Use word boundaries.** `\bbomb\b` not `bomb` — the latter matches
  `bombastic`.
- **Bias to `DECEIVE` over `HARM` for prompt-injection.** Easier to
  surface "this looks like injection" than "this is harmful".

## Roadmap

| When | What |
|---|---|
| **Phase 2 week 6** | Public registry at `packs.orivael.dev` — browse + install packs over HTTP without touching the filesystem. |
| **Phase 2 week 7** | More first-party packs: PCI-DSS, COPPA, GDPR Article 5, SEC Rule 10b-5 (financial advice). |
| **Phase 2 week 8** | Third-party publisher keys (managed via Stripe + AWS KMS). Smithery.ai discovery. |
| **Phase 3+** | Pack-driven Skill Pack Builder for non-Firewall products (Data Gate, CallGuard). |
