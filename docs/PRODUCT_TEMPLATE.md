# Axiom Product — Template

Copy this file as a starting point for new product specs. Drop the new
file into `docs/PRODUCTS.md` as a `## Axiom Certify · <Service Name>`
section, or keep it as a standalone `docs/products/<name>.md` if it
grows large.

Replace every `<placeholder>` with the actual content. Delete sections
that don't apply to your product.

---

## Axiom Certify · `<Service Name>`

**Tagline:** `<one-sentence elevator pitch, 12 words max>`

**Status:** `idea | spec'd | partial-implementation | shipped`

**Last updated:** `<YYYY-MM-DD>`

---

### What the customer submits

`<exactly what arrives at the start of the engagement — a model
endpoint, a system prompt, an .axiom spec, a chat transcript, a
codebase, a config file, etc.>`

### What the customer receives

`<bulleted list of concrete deliverables — a PDF report, a JSON
audit log, a signed badge, a Slack webhook, a remediation
playbook, etc.>`

- ...
- ...
- ...

### Backend modules used

Map each deliverable to the existing AXIOM module(s) that produce it.
This forces you to notice what's already built before opening the
editor.

| Deliverable | Module / endpoint | Status |
|---|---|---|
| `<deliverable>` | `<module.py>` or `<POST /endpoint>` | shipped / exists / missing |

### Gaps to ship

The minimum work needed to deliver to the first paying customer. Be
honest — if there's no intake form, no PDF generator, no payment
hookup, list them.

- `<missing piece 1>`
- `<missing piece 2>`

### Target customer + pricing

- **Who buys this:** `<role + company size + use case>`
- **What pain it solves:** `<one sentence>`
- **Pricing model:** `<one-time / subscription / per-audit / tiered>`
- **Ballpark:** `<not committed — just orienting>`

### Cross-references

- Related .axiom specs: `<paths>`
- Related modules: `<paths>`
- Related docs: `<paths>`
- ORVL number (if patent-aligned): `<ORVL-XXX>`

### Notes / open questions

Free-form. Things you want to think about later. Hand-offs to other
products. Risks. Out-of-scope decisions.

---

**Why this template exists:**
Every new product idea should answer the same questions before code is
written. The template forces the mapping of "deliverables → existing
modules → real gaps" so we don't accidentally rebuild what's already
in the repo, and so the brand stays cohesive across products.
