# Custom policies

Each tenant can layer their own block patterns on top of the default
classifier. The default classifier stays as the baseline — your policy
*adds* restrictions, removes them, or restricts to a whitelist.

Policies are edited at <https://firewall.orivael.dev/dashboard/policy>.

## Schema (version 1)

```json
{
  "version": 1,
  "additional_block_patterns": [
    {"class": "HARM",    "regex": "leak the customer list"},
    {"class": "DECEIVE", "regex": "you are a real person"}
  ],
  "disabled_default_classes": ["REFUSE"],
  "allow_only_classes": null
}
```

| Field | Type | Description |
|---|---|---|
| `version` | int | Always `1` for this release. |
| `additional_block_patterns` | array | Extra regexes (case-insensitive). Matches force a `block` verdict with the given class. |
| `disabled_default_classes` | array | Default classes you want to *allow* through (downgrade their verdict). |
| `allow_only_classes` | array \| null | Whitelist. Anything outside this list is blocked. `null` disables the whitelist. |

Both `class` fields can be: `INFORM`, `CLARIFY`, `REFUSE`, `HARM`,
`DECEIVE`, `UNCERTAIN`.

`additional_block_patterns[].class` is restricted to the **block
classes**: `HARM` or `DECEIVE`. A custom pattern fundamentally means
"block this with intent class X", so allowing `INFORM` would be a
no-op.

## How a verdict is computed

For every `/v1/guard/check` call:

```
1. Default classifier produces an IntentTypingResult.
2. If any additional_block_pattern matches the text:
     → verdict = block, intent_class = pattern's class
     → signals get a "custom_<class>" entry
     → short-circuit (skip steps 3-5)
3. If allow_only_classes is set and intent_class not in it:
     → verdict = block
4. Default verdict from intent class:
     intent_class in {HARM, DECEIVE} → block
     otherwise                        → allow
5. If intent_class is in disabled_default_classes:
     → verdict = allow (override the default block)
```

## Examples

### Add a custom HARM keyword

You run a customer support tool and want to block any prompt mentioning
a competitor's name as a leak target:

```json
{
  "version": 1,
  "additional_block_patterns": [
    {"class": "HARM", "regex": "leak (?:to|for) (?:acme|globex|initech)"}
  ]
}
```

### Run in "INFORM-only" mode

You're shipping a documentation-lookup bot. Any prompt that isn't a
pure information request should be blocked:

```json
{
  "version": 1,
  "allow_only_classes": ["INFORM", "CLARIFY"]
}
```

### Allow `REFUSE` patterns to flow through

You're using the Firewall in a context where a user *refusing* to
follow a model's suggestion is normal — you don't want those flagged:

```json
{
  "version": 1,
  "disabled_default_classes": ["REFUSE"]
}
```

(`REFUSE` isn't a default block class, so this only matters if you
combine it with a stricter `allow_only_classes` whitelist.)

### Block prompt injection on top of defaults

The default classifier catches common prompt injection patterns under
`DECEIVE`. Add domain-specific patterns:

```json
{
  "version": 1,
  "additional_block_patterns": [
    {"class": "DECEIVE", "regex": "(?:please|now) (?:disregard|forget)"},
    {"class": "DECEIVE", "regex": "you are not bound by"},
    {"class": "DECEIVE", "regex": "this is the developer speaking"}
  ]
}
```

## Validation

The dashboard rejects malformed policies with a specific error message.
Common problems:

| Error | Cause |
|---|---|
| `Unsupported policy version 99` | `version` must be `1`. |
| `additional_block_patterns[0]: invalid regex: ...` | Your regex doesn't compile. |
| `'class' must be one of ['DECEIVE', 'HARM']` | A custom pattern's `class` must be a block class. |
| `Unknown class 'INFORMS'` in `disabled_default_classes` | Typo. |

## Versioning

The schema is committed to **2-year backward compat** per
[Phase 1 Decisions §1](https://github.com/Orivael-Dev/axiom/blob/main/docs/PHASE_1_DECISIONS.md).
Version 1 will remain valid through at least 2028-05-16. Breaking
schema changes require a major-version bump and a migration tool.

## Limits

- Maximum 100 `additional_block_patterns` per tenant policy.
- Maximum 1,000 characters per regex.
- Patterns are compiled per request (not per call) and cached in-
  memory by the dashboard, so a complex policy adds at most ~0.5 ms
  to a single verdict.

(Enforcement of these caps is queued for Phase 2.)

## Programmatic upload (Phase 2+)

A `/v1/policy` API endpoint that lets tenants upload + version their
policy programmatically is planned for Phase 2. For now: dashboard.
