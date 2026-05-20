"""
Enrich pf_grants with LLM-generated semantic descriptions.

For each grant where enriched_embed_text is NULL, calls Claude to write
a one-sentence description of what the grantee does based on their name,
location, and grant purpose. Stores the result in enriched_embed_text.

The embed script uses enriched_embed_text when available, falling back
to embed_text when not — so this can be run incrementally and partial
enrichment is safe.

Schema change required before first run
----------------------------------------
ALTER TABLE `<project>.<dataset>.pf_grants`
ADD COLUMN IF NOT EXISTS enriched_embed_text STRING;

Usage
-----
python enrich_990pf.py \
    --project_id ai-agent-platform-496418 \
    --dataset_id query_dataset \
    [--limit 500] \
    [--batch_size 20] \
    [--dry_run]
"""

import argparse
import logging
import sys
import time
from itertools import islice

import anthropic
from google.cloud import bigquery

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are helping build a grant search tool for nonprofit 
fundraisers. Given a foundation grant record, write one specific sentence 
describing what the grantee organization likely does — the type of work, 
population served, and location if clear. 

Rules:
- Be specific, not generic. "provides cancer research and patient care" 
  is good. "provides support to the community" is not.
- If the grantee name makes the work obvious (e.g. MOFFITT CANCER CENTER),  
  use that signal even if the purpose is vague.
- If the purpose is specific, use it. If it's generic like 
  "PROVIDE OPERATING FUNDS", ignore it and infer from the name.
- One sentence only. No preamble. No "This organization..."."""

USER_TEMPLATE = """Foundation: {filer_name} ({filer_state})
Grantee: {grantee_name} ({grantee_state})
Purpose: {purpose}

Describe what the grantee does:"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def batched(items: list, size: int):
    it = iter(items)
    while chunk := list(islice(it, size)):
        yield chunk


def build_enriched_text(
    filer_name: str | None,
    filer_state: str | None,
    grantee_name: str | None,
    grantee_state: str | None,
    description: str,
) -> str:
    """Combine foundation + grantee + LLM description into final embed text."""
    filer_part   = f"{filer_name} ({filer_state})"   if filer_state   else filer_name or ""
    grantee_part = f"{grantee_name} ({grantee_state})" if grantee_state else grantee_name or ""
    return " | ".join(p for p in [filer_part, grantee_part, description] if p)


def call_claude(
    client: anthropic.Anthropic,
    row: dict,
) -> str | None:
    """
    Call Claude to generate a description for one grant row.
    Returns the description string, or None if the call fails.
    """
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=120,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": USER_TEMPLATE.format(
                    filer_name=row.get("filer_name") or "Unknown foundation",
                    filer_state=row.get("filer_state") or "unknown state",
                    grantee_name=row.get("grantee_name") or "Unknown grantee",
                    grantee_state=row.get("grantee_state") or "unknown state",
                    purpose=row.get("grant_purpose") or "not specified",
                )
            }]
        )
        return response.content[0].text.strip()
    except anthropic.RateLimitError:
        log.warning("Rate limited — sleeping 10s...")
        time.sleep(10)
        return None
    except Exception as exc:
        log.warning("Claude call failed for grant %s: %s", row.get("grant_id"), exc)
        return None


def save_batch_simple(
    bq: bigquery.Client,
    project_id: str,
    dataset_id: str,
    updates: list[dict],
    dry_run: bool,
) -> None:
    """
    Update enriched_embed_text one row at a time.
    Simple and reliable — BigQuery DML is fast enough for small batches.
    """
    if not updates:
        return

    if dry_run:
        for u in updates[:3]:
            log.info(
                "[dry_run] %s → %s",
                u["grant_id"][:12],
                u["enriched_embed_text"][:100],
            )
        if len(updates) > 3:
            log.info("[dry_run] ...and %d more rows", len(updates) - 3)
        return

    table = f"{project_id}.{dataset_id}.pf_grants"

    for u in updates:
        # Escape single quotes in the generated text
        safe_text = u["enriched_embed_text"].replace("'", "\\'")
        bq.query(f"""
            UPDATE `{table}`
            SET enriched_embed_text = '{safe_text}'
            WHERE grant_id = '{u["grant_id"]}'
        """).result()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich pf_grants with LLM-generated descriptions"
    )
    parser.add_argument("--project_id",  required=True)
    parser.add_argument("--dataset_id",  required=True)
    parser.add_argument("--limit",       type=int, default=500,
                        help="Max grants to enrich in this run")
    parser.add_argument("--batch_size",  type=int, default=20,
                        help="Grants per Claude call batch (writes to BQ after each)")
    parser.add_argument("--call_delay",  type=float, default=0.5,
                        help="Seconds to sleep between Claude calls (default 0.5)")
    parser.add_argument("--dry_run",       action="store_true",
                        help="Call Claude and log output but don't write to BigQuery")
    parser.add_argument("--dry_run_limit", type=int, default=5,
                        help="Max rows to process during --dry_run (default 5)")
    args = parser.parse_args()

    bq     = bigquery.Client(project=args.project_id)
    client = anthropic.Anthropic()

    grants_table = f"{args.project_id}.{args.dataset_id}.pf_grants"

    # ── Fetch grants that haven't been enriched yet ───────────────────────────
    # During dry run cap at dry_run_limit so we don't call Claude
    # hundreds of times just to preview output
    effective_limit = args.dry_run_limit if args.dry_run else args.limit

    rows = [
        dict(r) for r in bq.query(f"""
            SELECT
                grant_id,
                filer_name,
                filer_state,
                grantee_name,
                grantee_state,
                grant_purpose,
                embed_text
            FROM `{grants_table}`
            WHERE enriched_embed_text IS NULL
              AND grantee_name IS NOT NULL
            ORDER BY grant_id
            LIMIT {effective_limit}
        """).result()
    ]

    if not rows:
        log.info("No grants need enrichment — all have enriched_embed_text.")
        return

    log.info(
        "Enriching %d grants in batches of %d...",
        len(rows), args.batch_size,
    )

    # ── Process in batches ────────────────────────────────────────────────────
    total_enriched = total_failed = 0

    for batch_num, batch in enumerate(batched(rows, args.batch_size), start=1):
        log.info(
            "Batch %d — %d grants...",
            batch_num,
            len(batch),
        )

        updates = []

        for row in batch:
            description = call_claude(client, row)
            time.sleep(args.call_delay)  # prevent rate limiting

            if not description:
                total_failed += 1
                continue

            enriched = build_enriched_text(
                filer_name=row.get("filer_name"),
                filer_state=row.get("filer_state"),
                grantee_name=row.get("grantee_name"),
                grantee_state=row.get("grantee_state"),
                description=description,
            )

            updates.append({
                "grant_id":           row["grant_id"],
                "enriched_embed_text": enriched,
            })

            log.info(
                "  %s → %s",
                (row.get("grantee_name") or "")[:40],
                enriched[:100],
            )

        # Write this batch to BigQuery before moving to the next
        # so a crash mid-run doesn't lose completed work
        save_batch_simple(
            bq, args.project_id, args.dataset_id, updates, args.dry_run
        )

        total_enriched += len(updates)
        log.info(
            "  Batch %d done — %d enriched, %d failed so far",
            batch_num, total_enriched, total_failed,
        )

        # Small pause between batches to avoid rate limits
        if batch_num * args.batch_size < len(rows):
            time.sleep(1)

    log.info(
        "Done — %d enriched, %d failed (will retry on next run)",
        total_enriched, total_failed,
    )


if __name__ == "__main__":
    main()