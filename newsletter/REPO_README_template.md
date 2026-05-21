# Governance Under Question

Independent updates on AI governance, security, bias, and reports /
benchmarks. Companion publication for original research papers.

**Live site:** https://orivael-dev.github.io/governance-under-question/
(or https://governance.orivael.dev when the custom domain is pointed)

## What this publication is

A newsletter that refuses to do three things common AI-governance
publications do:

- Score vendors based on policy documents alone
- Report adoption claims without a reproducible eval
- Editorialize without marking the opinion as one

Every issue links to its underlying artifact: a fresh benchmark run,
a public report, a reproducible experiment, or a working paper queued
for submission to a venue. Methodology ships alongside results.

## Repository layout

```
.
├── index.html                     ← landing page (hero / topics / issues / papers / subscribe)
├── styles.css                     ← shared design system
├── og.png                         ← Open Graph image (1200x630)
├── feed.xml                       ← RSS 2.0 — readers + Scholar index this
├── .nojekyll                      ← tells GitHub Pages not to Jekyll-process
├── issues/
│   ├── 001-evidence-not-vibes.html
│   └── …                          ← one HTML file per issue
└── papers/
    ├── 2026-event-tokens.html
    └── …                          ← one HTML file per paper landing page
```

## How to ship a new issue

```bash
# Copy the template:
cp issues/001-evidence-not-vibes.html issues/00X-your-slug.html

# Edit the new file — minimal changes:
#   <title>                  → post title
#   <meta name="description"> + og:description
#   <article-meta>           → issue number + date + tag
#   <h1>                     → headline
#   <p class="dek">          → subtitle
#   body                     → 3-5 sections of real content
#   prev/next links          → at the bottom

# Add an entry to index.html under #issues (copy one of the existing
# <article class="issue"> blocks and update the slug + summary).

# Add an <item> to feed.xml so RSS readers pick it up.

git add issues/00X-your-slug.html index.html feed.xml
git commit -m "Issue 00X: <title>"
git push
```

GitHub Pages picks up the change within ~30 seconds.

## How to ship a new paper

```bash
cp papers/2026-event-tokens.html papers/YYYY-your-slug.html
# Edit:
#   <title> / og:* / paper-status class (working|in-review|drafting)
#   .paper-actions buttons      → PDF + code + companion issue links
#   .abstract                   → the real abstract
#   body sections               → the actual paper content
#   <details class="bibtex">    → citation block
#
# Drop the PDF alongside (e.g. papers/YYYY-your-slug.pdf) so the
# "Download PDF" button points at a real file.

# Add to index.html #papers + feed.xml as above.
```

## Tone rules (for me, when I'm tempted to soften)

- No hot takes on model releases without an eval to back them
- No vendor scoring based on policy documents alone
- No editorial framing dressed up as analysis (mark opinions explicitly)
- No sponsored deep dives, no affiliate links
- Cite primary sources; link to the data; publish methodology

## Subscribe

The form on the landing page is pointed at `action="#"` by default
— wire it up to one of: Buttondown / Beehiiv / Substack / MailerLite
/ a custom backend. Each provider gives you a form snippet to drop
in.

## License

Editorial content (issues + papers): CC BY 4.0 unless otherwise noted
per-issue. The website code (HTML/CSS) is MIT.

## Provenance

The site was scaffolded as a subfolder of
[github.com/Orivael-Dev/axiom](https://github.com/Orivael-Dev/axiom)
and moved out into this dedicated repo at first publication. Issues
that cite AXIOM-specific artifacts link back to that repo for
implementation.
