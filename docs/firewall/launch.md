# Soft-launch playbook

How to flip the switch on `firewall.orivael.dev` for the 20-person
developer waitlist.

## T-7 days: production deploy

- [ ] Build + push the v0.1.0 image to ECR.
- [ ] Run `deploy/firewall/cloudformation.yaml` against the prod
      account. Verify `/healthz` and `/readyz` from the public URL.
- [ ] Run `scripts/stripe_setup.py` in **test mode** first.
      Sanity-check the products + prices in the Stripe dashboard.
- [ ] Re-run `scripts/stripe_setup.py` with the live secret key.
      Populate the Secrets Manager entries.
- [ ] Create the webhook endpoint at
      `https://firewall.orivael.dev/billing/webhook` and copy the
      signing secret into `axiom-firewall/stripe-webhook-secret`.
- [ ] Test a full upgrade → cancel → upgrade cycle with a real card
      in **live mode** (refund yourself afterward).
- [ ] Verify CloudWatch Logs are flowing.
- [ ] Set up CloudWatch alarms for ALB `HTTPCode_Target_5XX_Count`,
      `TargetResponseTime` p99, and `UnHealthyHostCount`.
- [ ] Publish docs to `docs.orivael.dev/firewall/` (build via MkDocs
      Material, Docusaurus, or Astro Starlight — the markdown is
      generator-agnostic).
- [ ] Publish the Python SDK to PyPI:
      `git tag py-sdk-v0.1.0 && git push origin py-sdk-v0.1.0`
- [ ] Publish the TypeScript SDK to npm:
      `git tag ts-sdk-v0.1.0 && git push origin ts-sdk-v0.1.0`

## T-3 days: invite-list prep

- [ ] Export the 20-person waitlist into a CSV.
- [ ] Personalize the [beta-invite email template](#beta-invite-email).
- [ ] Set up <support@orivael.dev> with auto-acknowledge + on-call
      rotation.
- [ ] Set up the status page at `status.orivael.dev` (StatusPage,
      Atlassian, or a static page) showing ALB latency + 5xx rate.

## T-0: send invites

Mail-merge from the CSV. Stagger sends across a few hours so initial
support volume is manageable.

## T+1: morning-after check

- [ ] How many invitees signed up?
- [ ] How many ran their first `/v1/guard/check` call within 24 hours?
- [ ] CloudWatch metrics: avg latency, error rate, total calls?
- [ ] Any reports of broken signups / confusing errors?

## T+7: first weekly review

- [ ] Aggregate intent-class distribution across all tenants —
      where are the false positives / false negatives?
- [ ] Top three "wish you had X" requests from beta users.
- [ ] Conversion: free → paid?
- [ ] Refine Week-5+ roadmap (Phase 2 prep) based on signals.

---

## Beta invite email

> Subject: You're in — Axiom Intent Firewall is live for you.
>
> Hi {first_name},
>
> Thanks for signing up to the Axiom Intent Firewall beta. We're
> sending this to a small group of developers because we want
> high-signal feedback before the public launch.
>
> Start here: <https://firewall.orivael.dev/signup>.
>
> What you get on the free tier:
> - 1,000 API calls per month, no card required
> - The full default classifier (HARM + DECEIVE block patterns,
>   including scam-call payment-fraud and prompt-injection coverage)
> - The dashboard, custom-policy editor, and both SDKs
>   (Python: `pip install axiom-firewall`,
>    TypeScript: `npm install @axiom/firewall`)
>
> 5-minute quickstart: <https://docs.orivael.dev/firewall/quickstart>
>
> What we'd love feedback on:
> 1. Anything that confused you on the way to your first verdict.
> 2. A false positive or false negative that surprised you (please
>    send the prompt + the verdict).
> 3. What's missing — features, integrations, docs.
>
> Reply to this email or write us at <feedback@orivael.dev>. I read
> every one.
>
> — {your_name}
> P.S. If anything breaks, mail <support@orivael.dev>. The on-call
> rotation is human-staffed during U.S. business hours and on-call
> 24/7 for production outages this week.

---

## Onboarding email (T+24h, conditional on no `/v1/guard/check` calls)

> Subject: Stuck somewhere?
>
> Hi {first_name},
>
> Quick check-in — I see you signed up but haven't run your first
> `/v1/guard/check` call yet. Anything I can clear up?
>
> The two most common stumbles:
>
> 1. Copying the API key. We only show the secret once after creation;
>    if you missed it, **Dashboard → Create key** again and you're
>    good.
>
> 2. The `Authorization` header format. It's literally
>    `Authorization: Bearer axfw_yourkey`, no quotes, no extra
>    whitespace.
>
> If you'd rather skip the curl step, the SDKs do it for you:
>
> ```python
> from axiom_firewall import Client
> c = Client(api_key="axfw_...")
> print(c.check("What is the weather today?"))
> ```
>
> Reply with the error message and I'll unstick you within a few hours.
>
> — {your_name}
