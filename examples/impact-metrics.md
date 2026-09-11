# Business Impact Metrics — Eligibility, Sources & Assumptions

This file is the ONLY source of projected figures allowed on the resume, and it is read directly
by tailor.py: **a project with a `###` section under ELIGIBLE below may carry its projected figure;
a project without one may not.** Delete a section and that project's numbers become lint errors.

The file is optional. Delete it entirely and the pipeline simply never adds a projected figure —
every dollar amount then has to come from your own source bullets, and anything else is flagged.

Headings matter to the parser. Keep `## ELIGIBLE`, `## NOT ELIGIBLE`, one `### <Project name>` per
project, and one `**Approved claim:**` line per eligible project. The `###` heading should share
distinctive words with the project's `title` in content.json, because that is how the lint matches
a bullet's figure to its permission.

---

## The test

A projected figure is allowed only if BOTH hold:

1. **The work targets a real system whose scale is citable from external data** — real institutions,
   real populations, real published revenue. Not a hypothetical customer base for a product that
   does not exist.

2. **The figure sizes the opportunity in that system.** It says where impact *could* be generated —
   the scale of the problem being addressed. It must never:
   - price a business that does not exist (brand equity at exit, ARR at X% adoption, consulting scope), or
   - assume an efficacy rate for something that was never built ("our AI saves 60% of review time"
     for a Figma prototype).

Framing is always **"projected"**, **"addressable"**, or **"targeting"** — never a realized result.
If pressed in an interview, walk through the top two inputs; that is enough to show the reasoning is sound.

A bullet with no metric is strictly better than a bullet with a metric that cannot be defended.

---

## ELIGIBLE

### Transit Arrival Board

**Approved claim:** 60,000+ daily riders with low vision addressable in this transit system alone

| Input | Value | Source |
|---|---|---|
| System weekday boardings | 400,000 | The agency's own published ridership report |
| Share of riders with vision impairment | ~3% | National survey prevalence for the adult population |
| Stops with a real-time display | 1,100 | Agency open-data feed |
| Rider count at the tested stop | ~2,400/day | Agency stop-level ridership data |

**Model:**
- 400,000 weekday boardings × 3% ≈ **12,000 boardings/day by riders with vision impairment**
- Across the 5 comparable agencies in the region: ~60,000/day

**Why it passes:** the ridership base is a real agency's published numbers, and the redesign targets
that exact display. The figure sizes who is affected by the problem — it never claims the prototype
served them.

---

## NOT ELIGIBLE — no projected figure, ever

Recorded so the reasoning survives and the numbers are not quietly reintroduced.

### Pantry — NO METRIC

A concept app with no users and no product. There is no real system whose scale can be cited, so it
fails test (1) outright. An earlier draft claimed "$4M ARR at 1% household adoption", which priced an
imaginary business. Deleted. The work stands on the three-way input test with 8 participants.

### Ledger — NO METRIC

A concept flow, never built. An earlier draft claimed "cuts 30% off payment collection time" — an
efficacy rate for software that does not exist. That is a claim about the artifact's performance,
not the scale of the problem. Fails test (2). Deleted. The work stands on the 5 freelancer interviews.

---

## Rules for use

- Attach a projected figure ONLY to a project with a `###` section under ELIGIBLE above, using that
  section's approved claim.
- One projected figure per bullet, maximum. Never stack them.
- Never fabricate a precise percentage.
- Only add a figure where a REAL bullet carries no number. A projected figure never displaces a real,
  source-attested fact (counts, minutes, users, votes, components, test scores).
- For any project not listed above, the correct number of invented figures is zero.
