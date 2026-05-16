# Training manual — Skill Pack format

> A signed JSON file that bundles a Firewall policy with metadata.
> `format_version: "1.0"` is committed to backward-compatibility
> through **2028-05-16** per Phase 1 Decision §1.

## What it is

A single `pack.json` document. Defines:

1. **Identity** — name, title, version, author, license, homepage.
2. **Policy** — a `TenantPolicy` body (block patterns, disabled classes, whitelist).
3. **Compatibility hints** — `tested_against` declares which Firewall versions the pack has been verified against.
4. **Signature** — HMAC-SHA256 over the canonical payload, proving who signed it.

Source: `axiom_firewall/skill_pack.py`. Type: `SkillPackManifest` dataclass.

## Who it's for

- **Pack authors** (us, today; third parties in Phase 2 week 7+).
- **Reviewers** — anyone evaluating whether a pack is correct for
  their domain.
- **Customer success** — translating "I work in healthcare" into
  "you want `hipaa-intake`".

## Why a versioned, signed format

Three reasons:

1. **Backward compatibility.** Once a customer's production runtime
   reads `format_version: "1.0"`, they don't want a future change to
   break them. We commit to 2 years.
2. **Tamper-evidence.** A man-in-the-middle proxy can't substitute
   a corrupted pack — the signature would fail to verify.
3. **Attribution.** First-party packs are signed with the master
   key. Third-party packs (Phase 2 w7+) will use KMS-issued
   publisher keys.

## How it works

### The manifest schema

```json
{
  "format_version": "1.0",
  "name":           "fdcpa",
  "title":          "FDCPA — Fair Debt Collection Practices Act",
  "description":    "Block FDCPA-prohibited debt-collection patterns.",
  "version":        "0.1.0",
  "author":         "Orivael Dev",
  "license":        "MIT",
  "homepage":       "https://docs.orivael.dev/firewall/packs/fdcpa",
  "tags":           ["compliance", "finance", "us-federal"],
  "tested_against": ["axiom-firewall>=0.1.0"],
  "policy": {
    "version": 1,
    "additional_block_patterns": [
      {"class": "HARM", "regex": "warrant\\s+for\\s+your\\s+arrest"}
    ],
    "disabled_default_classes": [],
    "allow_only_classes": null
  },
  "signature": "808f53b24ac8acdf..."
}
```

### Field constraints

| Field | Required | Constraint |
|---|---|---|
| `format_version` | yes | Exactly `"1.0"` for this release |
| `name` | yes | `^[a-z][a-z0-9-]{1,63}$` — kebab-case, max 64 chars |
| `title` | yes | Free-form human-readable |
| `description` | yes | Free-form paragraph |
| `version` | yes | Semver — `0.1.0`, `1.0.0-rc.1`, etc. |
| `author` | yes | Publisher name |
| `license` | yes | SPDX identifier |
| `homepage` | no | URL or omitted |
| `tags` | no | Array of free-form strings |
| `tested_against` | no | Array of version-range strings |
| `policy.version` | yes | Always `1` |
| `policy.additional_block_patterns[].class` | yes | `"HARM"` or `"DECEIVE"` only |
| `policy.additional_block_patterns[].regex` | yes | Valid Python `re` regex |
| `signature` | required at serve-time | hex HMAC-SHA256 |

`parse()` rejects any manifest that violates these. The verdict path
NEVER sees a malformed pack.

### Canonical form (the signing surface)

```python
canonical = json.dumps(
    manifest.to_dict_without_signature(),
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=True,
)
signature = HMAC_SHA256(key, canonical.encode("utf-8"))
```

Three properties that matter:

1. **Sorted keys** — adding `homepage` to a manifest that didn't
   have it doesn't break signatures elsewhere.
2. **Compact separators** — no whitespace; "pretty" and "minified"
   JSON sign the same.
3. **ASCII-only** — non-ASCII content is `\u`-escaped, so different
   serializers produce the same bytes.

The signature is computed over the canonical form, then PASTED into
the manifest as the `signature` field. The signature field itself
is excluded from the canonical form (recursion would be impossible
otherwise).

### Three layers of defense

```
        ┌──────────────────────────┐
        │ Layer 1: SIGN time       │  scripts/sign_packs.py
        │ Author runs the signer.  │
        │ Output is a pack.json    │
        │ with a valid signature.  │
        └────────────┬─────────────┘
                     │ push to registry
                     ▼
        ┌──────────────────────────┐
        │ Layer 2: SERVE time      │  axiom_packs.server
        │ Registry verifies before │
        │ each response. Bad packs │
        │ are silently dropped.    │
        └────────────┬─────────────┘
                     │ HTTP GET to dashboard
                     ▼
        ┌──────────────────────────┐
        │ Layer 3: INSTALL time    │  registry_client +
        │ Dashboard re-verifies    │  dashboard.py
        │ before writing to the    │
        │ tenant_policy table.     │
        └──────────────────────────┘
```

A tampered file at any layer is caught.

## Common workflows

### Workflow A: Author a new pack

```bash
# Start from a template
cp packs/customer-support-base/pack.json packs/my-domain/pack.json

# Edit fields: name, title, description, version, regex patterns
$EDITOR packs/my-domain/pack.json

# Sign with the master key
AXIOM_MASTER_KEY=<hex> python scripts/sign_packs.py packs/my-domain

# Restart the dashboard or the registry — pack appears.
```

### Workflow B: Bump a pack's version

1. Edit `pack.json`, bump `version` from `0.1.0` to `0.2.0`.
2. (Optional) Add the new patterns under `additional_block_patterns`.
3. Re-run `scripts/sign_packs.py`. The script is idempotent — it
   only rewrites packs whose signature changed.
4. (Recommended for traceability) Copy the pre-release pack to
   `packs/<name>/0.1.0/pack.json` so the registry can still serve
   the old version at `/v1/packs/<name>/0.1.0`.

### Workflow C: Validate a third-party pack without installing

```python
from axiom_firewall.skill_pack import SkillPackManifest, verify_first_party

manifest = SkillPackManifest.parse(open("third-party.json").read())
print(manifest.name, manifest.version, manifest.author)
print(f"Signed by first-party? {verify_first_party(manifest)}")
print(f"Patterns:")
for cls, pattern in manifest.to_policy().additional_block_patterns:
    print(f"  [{cls}] {pattern.pattern}")
```

### Workflow D: Customize an installed pack

When a tenant installs a pack:
1. The pack's `policy` body is written into their `tenant_policy` table.
2. The pack's manifest (with lineage info) is written into their
   `installed_pack` table.

So when the tenant subsequently opens `/dashboard/policy`, they see
the pack's JSON, can edit it freely, and on save the edits supersede
the original. The `installed_pack` record persists so the dashboard
can show "based on `fdcpa@0.1.0`" with a "Restore" option.

## Test scenarios

| # | Scenario | Expected |
|---|---|---|
| 1 | `SkillPackManifest.parse(valid_dict)` | returns manifest |
| 2 | Parse with missing `author` | `ValueError("missing required field 'author'")` |
| 3 | Parse with `format_version: "2.0"` | `ValueError("Unsupported policy version")` |
| 4 | Parse with `name: "Invalid Name"` | `ValueError("must be lowercase")` |
| 5 | Parse with `version: "1.0"` (no patch) | `ValueError("must be semver")` |
| 6 | Parse with invalid regex `"("` in a pattern | `ValueError("invalid regex")` |
| 7 | `sign_first_party(payload)` + verify | True |
| 8 | Tamper the policy, keep old signature, verify | False |
| 9 | Sign with key A, verify with key B | False |
| 10 | Set `signature: "garbage"` on the input dict, sign | Same signature as if the field was absent |
| 11 | `install_pack(tenant, manifest)` | tenant_policy table reflects pack's policy |
| 12 | Install one pack, then another | Second replaces first |
| 13 | Read corrupt `installed_pack` row | `get_installed_pack()` returns None (graceful) |

All covered by `tests/test_axiom_firewall_skill_pack.py` (17 tests).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "Pack has invalid or missing signature — refusing install" | Pack was edited after signing | Re-run `scripts/sign_packs.py` |
| "Unsupported pack format_version" | Future version not yet shipped | Bump the dashboard or use the older pack version |
| Pack doesn't appear in `/dashboard/packs` | Either signature failed verification OR `parse()` rejected the manifest | Run `python -c "from axiom_firewall.skill_pack import SkillPackManifest; SkillPackManifest.parse(open('pack.json').read())"` — see the exact error |
| `re.error: missing ), unterminated subpattern` | Bad escape in your regex | Remember JSON requires `\\s+` to mean `\s+` in regex |
| Pattern doesn't match what you expect | Regex is case-insensitive by default; word boundaries matter | Test against `re.compile(r, re.IGNORECASE).search(text)` directly |

## Authoring style guide

- **High precision over recall.** One false-positive a week destroys
  user trust. One false-negative a month is fine — the default
  classifier covers a lot.
- **Use word boundaries.** `\bbomb\b` not `bomb` — the latter matches
  `bombastic`.
- **Anchor identity claims to the speaker.** `i\s+(am|work)\s+for`
  catches the bad pattern; matching just `manager` catches every
  HR email.
- **Bias to DECEIVE for prompt injection.** Easier to surface "this
  is injection" than "this is harmful".
- **Test against real allowed text too.** Run your pack against the
  customer's last 1,000 ALLOWED prompts in shadow mode before
  promoting to enforce.

## Limitations / what's not here yet

- **No multi-file packs.** Single `pack.json`. Future versions may
  support a directory layout with `policy.json`, `README.md`,
  `examples/`, etc.
- **No semver constraints on `tested_against`.** Currently a free-
  form string. Phase 2 follow-up will treat it as a PEP 440
  specifier and refuse to install on incompatible Firewall versions.
- **No publisher attribution.** First-party only for v1. Third-party
  publisher keys via KMS come in Phase 2 week 7+.
- **No content beyond regex.** Future versions may support embedding
  rules, redaction patterns, prompt-template injections.

## Further reading

- Format definition: `axiom_firewall/skill_pack.py`
- Public reference: `docs/firewall/skill-packs.md`
- All nine first-party manifests: `packs/*/pack.json`
- Test fixtures as worked examples: `tests/test_axiom_firewall_skill_pack.py`
