# Operations runbook

The first-incident handbook for the Axiom Intent Firewall. Optimized
for "page goes off at 3 AM, what do I check first."

## Quick triage

```
1. https://firewall.orivael.dev/healthz   → 200?
2. https://firewall.orivael.dev/readyz    → 200?
3. CloudWatch Logs /ecs/axiom-firewall    → recent errors?
4. ECS service                            → desiredCount == runningCount?
```

## Common alarms

### `healthz` returning non-200

**Symptom:** ALB target group reports unhealthy.

**Check:**

```bash
aws ecs describe-services --cluster axiom-firewall \
    --services axiom-firewall \
    --query 'services[0].events[:5]'
```

Look for `was unable to place a task` (capacity issue), `failed
container health checks` (app problem), or `unable to consistently
start tasks` (likely image / env problem).

**Fix:**

- Capacity: bump task CPU/memory in CloudFormation `cpu` / `memory`
  parameters, redeploy.
- Health check fail: pull logs for the most recent task; usually a
  missing env var (`AXIOM_MASTER_KEY` is the #1 culprit).

### `readyz` returning 503

**Symptom:** Tasks are running but pulled from ALB rotation.

**Cause:** Registry DB write failed. Most likely EFS mount issue.

**Check:**

```bash
# Get a recent task and exec into it
aws ecs list-tasks --cluster axiom-firewall \
    --service-name axiom-firewall --desired-status RUNNING

aws ecs execute-command --cluster axiom-firewall \
    --task TASK_ARN --container firewall \
    --interactive --command "/bin/sh"

# Inside the task:
ls -la /data/tenants
sqlite3 /data/tenants/registry.db ".tables"
```

If EFS is read-only or unmounted, the access point or mount target
security group is misconfigured. Check CloudWatch `EFS` metrics:
`ClientConnections`, `BurstCreditBalance`.

### 5xx rate spike

**Symptom:** ALB `HTTPCode_Target_5XX_Count` alarm.

**Check:**

```bash
# Most recent errors
aws logs tail /ecs/axiom-firewall --follow --filter-pattern '"500"'
```

Common culprits:

| Pattern | Fix |
|---|---|
| `Stripe down` (in meter event logs) | Safe — verdict path is unaffected, see [billing resilience](billing.md#resilience). |
| `sqlite3.OperationalError: database is locked` | Concurrent writes to a tenant DB. SQLite WAL mode is enabled by default; if still hot, increase `busy_timeout` or shard. |
| `IntentClassifier` exceptions | Bug in the classifier — file an issue with the offending text (redacted). |

### Stripe webhook signature failures

**Symptom:** `/billing/webhook` returns 400 on every event; Stripe
dashboard shows failed deliveries.

**Cause:** `STRIPE_WEBHOOK_SECRET` doesn't match what Stripe has.

**Fix:** Copy the signing secret from
<https://dashboard.stripe.com/webhooks> → your endpoint → "Signing
secret" → into Secrets Manager
(`axiom-firewall/stripe-webhook-secret`). Force-restart the service to
re-read.

### A tenant reports "I can't sign in"

**Triage:**

1. Email exists?
   ```sql
   sqlite> SELECT tenant_id, email, tier FROM tenants WHERE email = ?;
   ```
2. Password reset? — not built yet (Phase 2). For now, reset manually:
   ```python
   from axiom_firewall.db import _conn, _registry_path
   from axiom_firewall.auth import hash_password
   new_hash = hash_password("temporary-password")
   with _conn(_registry_path()) as c:
       c.execute("UPDATE tenants SET pw_hash = ? WHERE email = ?",
                 (new_hash, "user@example.com"))
   ```
   Email the user the temporary password out-of-band; tell them to
   change it after login.

## Tenant data extraction (right-to-erasure / GDPR)

```bash
# In a running task:
TENANT_ID=$(sqlite3 /data/tenants/registry.db \
    "SELECT tenant_id FROM tenants WHERE email = 'user@example.com'")

# Export everything before deletion
mkdir /tmp/export-$TENANT_ID
sqlite3 /data/tenants/registry.db \
    ".dump tenants WHERE tenant_id = '$TENANT_ID'" \
    > /tmp/export-$TENANT_ID/registry.sql
cp /data/tenants/$TENANT_ID.db /tmp/export-$TENANT_ID/

# Then delete
rm /data/tenants/$TENANT_ID.db
sqlite3 /data/tenants/registry.db \
    "DELETE FROM tenants WHERE tenant_id = '$TENANT_ID'"

# Also cancel any active Stripe subscription:
stripe subscriptions cancel sub_xxxxx
```

A right-to-erasure certificate generator is queued for Phase 3 (it'll
use WeasyPrint per the [PDF generator decision](../PHASE_1_DECISIONS.md#4-pdf-report-generator)).

## Scaling

| Metric | At threshold | Action |
|---|---|---|
| CPU > 70% sustained 5min | Scale-out trigger | Bump `DesiredCount` in CFN parameter. |
| Latency p99 > 100 ms | Investigate | Most likely tenant DB lock contention. |
| Single tenant > 100M usage records | Postgres migration | Per Phase 1 Decisions §3. |

## Contact tree (internal)

| Role | Contact |
|---|---|
| On-call eng | (set up before launch) |
| Stripe billing issues | <support@orivael.dev>, then escalate to Stripe support if their fault |
| AWS infra | AWS Support — Business plan covers production |
| Security disclosure | <security@orivael.dev> — see [SECURITY.md](../SECURITY.md) |
