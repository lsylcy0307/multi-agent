"""
IRS 990-PF parser — ingests private foundation filings into BigQuery.

Writes to two tables:
  pf_foundations  one row per filing  (who the foundation is + financials)
  pf_grants       one row per grant   (who they funded + embed_text for Pinecone)

The embed_text field is built at parse time so the embed script just reads
and upserts — no extra joins needed. Generic purpose phrases like
"PROVIDE OPERATING FUNDS" are stripped so the vector carries real signal
(foundation name + grantee name + location). Specific purpose text is kept.

Usage
-----
python ingest_990pf.py \
    --project_id my-project \
    --dataset_id irs_data \
    --bucket_name my-bucket \
    --prefix raw/irs_990_xml/2024_990PF/ \
    [--limit 100] \
    [--dry_run]
"""

import argparse
import hashlib
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from google.cloud import bigquery, storage

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Namespace ─────────────────────────────────────────────────────────────────

_NS_URI = "http://www.irs.gov/efile"
_NS     = {"ns": _NS_URI}
_Q      = f"{{{_NS_URI}}}"

# ── Purpose phrases that carry no semantic meaning ────────────────────────────
# When a grant purpose matches one of these we omit it from embed_text so the
# vector is based on foundation + grantee names/locations instead of noise.

_GENERIC_PURPOSES = {
    "provide operating funds",
    "provide operating support",
    "provide operatings funds",   # common typo in filings
    "general operating support",
    "general support",
    "operating support",
    "support",
    "charitable contribution",
    "charitable purposes",
    "charitable support",
    "n/a",
    "none",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

def _first(node: ET.Element | None, *xpaths: str) -> str | None:
    if node is None:
        return None
    for xpath in xpaths:
        el = node.find(xpath, _NS)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
    return None

def _iter_first(node: ET.Element | None, *local_tags: str) -> str | None:
    if node is None:
        return None
    for tag in local_tags:
        for el in node.iter(f"{_Q}{tag}"):
            if el.text and el.text.strip():
                return el.text.strip()
    return None

def _is_generic_purpose(purpose: str | None) -> bool:
    return (purpose or "").strip().lower() in _GENERIC_PURPOSES

def _build_embed_text(
    filer_name: str | None,
    filer_state: str | None,
    grantee_name: str | None,
    grantee_state: str | None,
    purpose: str | None,
) -> str:
    """
    Build the text that will be embedded as a vector.

    Format: "{foundation} ({state}) | {grantee} ({state}) | {purpose}"
    Purpose is omitted when it's generic boilerplate — the signal then comes
    entirely from who the foundation is and who they chose to fund.

    Example (generic purpose):
      "ASHCOURT FAMILY FOUNDATION INC (FL) | MOFFITT CANCER CENTER (FL)"

    Example (specific purpose):
      "JOSEPH D SARGENT FUND (CT) | HARTFORD HOSPITAL (CT) |
       FUNDS ARE USED IN HARTFORD HOSPITAL'S CENTER FOR EDUCATION,
       SIMULATION AND INNOVATION"
    """
    filer_part   = f"{filer_name} ({filer_state})"   if filer_state   else filer_name
    grantee_part = f"{grantee_name} ({grantee_state})" if grantee_state else grantee_name

    parts = [filer_part, grantee_part]
    if purpose and not _is_generic_purpose(purpose):
        parts.append(purpose)

    return " | ".join(p for p in parts if p)


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_990pf(
    xml_bytes: bytes,
    filename: str,
    gcs_path: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Parse one 990-PF XML file.

    Returns
    -------
    (foundation_row, grant_rows)
    """
    root   = ET.fromstring(xml_bytes)
    header = root.find("ns:ReturnHeader", _NS)
    rd     = root.find(f"{_Q}ReturnData")

    # ── Identity ──────────────────────────────────────────────────────────────
    ein = _first(header, "ns:Filer/ns:EIN")
    org_name = _first(
        header,
        "ns:Filer/ns:BusinessName/ns:BusinessNameLine1Txt",
        "ns:Filer/ns:BusinessName/ns:BusinessNameLine1",
    )
    tax_period_end   = _first(header, "ns:TaxPeriodEndDt")
    tax_period_begin = _first(header, "ns:TaxPeriodBeginDt")
    tax_year_str     = _first(header, "ns:TaxYr")
    tax_period       = tax_period_end or tax_period_begin or "UNKNOWN"

    filing_year: int | None = None
    if tax_year_str and tax_year_str.isdigit():
        filing_year = int(tax_year_str)
    elif tax_period and len(tax_period) >= 4:
        filing_year = int(tax_period[:4])

    foundation_id = _sha(f"{ein}-{tax_period}-{filename}")

    # ── 990-PF body ───────────────────────────────────────────────────────────
    pf = rd.find(f"{_Q}IRS990PF") if rd is not None else None

    state          = _iter_first(pf, "OrgReportOrRegisterStateCd")
    fmv_assets     = _iter_first(pf, "FMVAssetsEOYAmt", "TotalAssetsEOYFMVAmt")
    total_expenses = _iter_first(pf, "TotalExpensesRevAndExpnssAmt")
    total_revenue  = _iter_first(pf, "TotalRevAndExpnssAmt")

    # ── SupplementaryInformationGrp (grants + application eligibility) ────────
    supp = pf.find(f"{_Q}SupplementaryInformationGrp") if pf is not None else None

    # OnlyContriToPreselectedInd = "X" means invitation-only
    only_preselected = supp.find(f"{_Q}OnlyContriToPreselectedInd") if supp is not None else None
    accepts_unsolicited: bool | None = None
    if only_preselected is not None:
        accepts_unsolicited = (only_preselected.text or "").strip() != "X"

    total_grants_paid = _first(supp, "ns:TotalGrantOrContriPdDurYrAmt")

    # ── Foundation row ────────────────────────────────────────────────────────
    foundation_row: dict[str, Any] = {
        "foundation_id":           foundation_id,
        "ein":                     ein or "UNKNOWN",
        "organization_name":       org_name,
        "filing_year":             filing_year,
        "tax_period":              tax_period,
        "state":                   state,
        "fmv_assets_raw":          fmv_assets,
        "total_revenue_raw":       total_revenue,
        "total_expenses_raw":      total_expenses,
        "total_grants_paid_raw":   total_grants_paid,
        "accepts_unsolicited_apps": accepts_unsolicited,
        "xml_filename":            filename,
        "gcs_path":                gcs_path,
        "created_at":              now_iso(),
    }

    # ── Grant rows ────────────────────────────────────────────────────────────
    grant_rows: list[dict[str, Any]] = []

    if supp is not None:
        for i, grp in enumerate(supp.findall(f"{_Q}GrantOrContributionPdDurYrGrp")):
            grantee_name = _first(
                grp,
                "ns:RecipientBusinessName/ns:BusinessNameLine1Txt",
                "ns:RecipientBusinessName/ns:BusinessNameLine1",
            )
            grantee_city  = _first(grp, "ns:RecipientUSAddress/ns:CityNm")
            grantee_state = _first(grp, "ns:RecipientUSAddress/ns:StateAbbreviationCd")
            purpose       = _first(grp, "ns:GrantOrContributionPurposeTxt")
            amount        = _first(grp, "ns:Amt")

            if not grantee_name:
                continue

            embed_text = _build_embed_text(
                filer_name=org_name,
                filer_state=state,
                grantee_name=grantee_name,
                grantee_state=grantee_state,
                purpose=purpose,
            )

            grant_rows.append({
                "grant_id":         _sha(f"{foundation_id}-grant-{i}"),
                "foundation_id":    foundation_id,
                "filer_ein":        ein or "UNKNOWN",
                "filer_name":       org_name,
                "filer_state":      state,
                "grantee_name":     grantee_name,
                "grantee_city":     grantee_city,
                "grantee_state":    grantee_state,
                "grant_amount_raw": amount,
                "grant_purpose":    purpose,
                "embed_text":       embed_text,
                "embedding_status": "PENDING",
                "created_at":       now_iso(),
            })

    return foundation_row, grant_rows


# ── BigQuery ──────────────────────────────────────────────────────────────────

def insert_rows(
    client: bigquery.Client,
    table_id: str,
    rows: list[dict[str, Any]],
    dry_run: bool = False,
) -> None:
    if not rows:
        return
    if dry_run:
        log.info("[dry_run] Would insert %d rows into %s", len(rows), table_id)
        return
    errors = client.insert_rows_json(table_id, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert failed for {table_id}: {errors}")


# ── Entry point ───────────────────────────────────────────────────────────────

def ingest_gcs_prefix(
    project_id: str,
    dataset_id: str,
    bucket_name: str,
    prefix: str,
    limit: int | None = None,
    dry_run: bool = False,
) -> None:
    storage_client = storage.Client(project=project_id)
    bq_client      = bigquery.Client(project=project_id)

    foundations_table = f"{project_id}.{dataset_id}.pf_foundations"
    grants_table      = f"{project_id}.{dataset_id}.pf_grants"

    xml_blobs = [
        b for b in storage_client.list_blobs(bucket_name, prefix=prefix)
        if b.name.endswith(".xml")
    ]
    if limit:
        xml_blobs = xml_blobs[:limit]

    log.info("Found %d XML files under gs://%s/%s", len(xml_blobs), bucket_name, prefix)

    ok = failed = 0

    for blob in xml_blobs:
        log.info("Ingesting: %s", blob.name)
        try:
            xml_bytes  = blob.download_as_bytes()
            foundation_row, grant_rows = parse_990pf(
                xml_bytes=xml_bytes,
                filename=Path(blob.name).name,
                gcs_path=f"gs://{bucket_name}/{blob.name}",
            )
            insert_rows(bq_client, foundations_table, [foundation_row], dry_run)
            insert_rows(bq_client, grants_table,      grant_rows,       dry_run)
            log.info(
                "  → %s (%s) | %d grants | unsolicited=%s",
                foundation_row["organization_name"],
                foundation_row["state"],
                len(grant_rows),
                foundation_row["accepts_unsolicited_apps"],
            )
            ok += 1
        except Exception as exc:
            log.warning("  ✗ Skipped %s: %s", blob.name, exc)
            failed += 1

    log.info("Done — %d succeeded, %d failed", ok, failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest 990-PF filings into BigQuery")
    parser.add_argument("--project_id",  required=True)
    parser.add_argument("--dataset_id",  required=True)
    parser.add_argument("--bucket_name", required=True)
    parser.add_argument("--prefix",      required=True)
    parser.add_argument("--limit",       type=int, default=None)
    parser.add_argument("--dry_run",     action="store_true")
    args = parser.parse_args()

    ingest_gcs_prefix(
        project_id=args.project_id,
        dataset_id=args.dataset_id,
        bucket_name=args.bucket_name,
        prefix=args.prefix,
        limit=args.limit,
        dry_run=args.dry_run,
    )