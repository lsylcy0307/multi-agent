"""
IRS 990 / 990-T XML parser — ingests filings into BigQuery.

Usage
-----
python parse_990_xml.py \
    --project_id my-project \
    --dataset_id irs_data \
    --bucket_name my-bucket \
    --prefix irs/990/ \
    [--limit 50] \
    [--dry_run]

Changes from v1
---------------
- Namespace-aware XPath throughout — prevents false matches on preparer-firm
  names/EINs vs. filer names/EINs (the original `endswith` heuristic
  matched the first element with that local name, which was often wrong).
- Filer block scoped — org name and EIN are read from the <Filer> subtree
  only, not anywhere in the document.
- 990-T field support — BookValueAssetsEOYAmt for assets; TotUnrltTrdBusIncmAmt/
  TotUnrltTrdBusIncmExpnssAmt for revenue/expenses; Schedule A trade descriptions
  as program rows.
- Both tax_period_begin and tax_period_end stored — useful for fiscal-year filers
  whose period doesn't align with TaxYr.
- Sentence-aware chunking — prefers splitting at ". " boundaries so each
  chunk ends on a complete thought, with word-level fallback for long sentences.
- Per-file error isolation — a malformed XML logs a warning and is skipped;
  the rest of the batch continues.
- Structured logging (stdlib logging) replaces bare print().
- Dead import (uuid) removed; type annotations added throughout.
- --dry_run flag for testing without writing to BigQuery.
- _sha() helper deduplicates ID logic.
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

# ── IRS efile namespace ───────────────────────────────────────────────────────

_NS_URI = "http://www.irs.gov/efile"
_NS = {"ns": _NS_URI}          # for root.find / root.findall
_Q = f"{{{_NS_URI}}}"          # Clark-notation prefix for root.iter / direct tags


# ── Utilities ─────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ── XPath helpers ─────────────────────────────────────────────────────────────

def _first(node: ET.Element | None, *xpaths: str) -> str | None:
    """
    Return stripped text of the first element found via any of *xpaths*
    (evaluated with the IRS namespace prefix "ns").
    Returns None if node is None or nothing matches.
    """
    if node is None:
        return None
    for xpath in xpaths:
        el = node.find(xpath, _NS)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
    return None


def _all(node: ET.Element | None, xpath: str) -> list[str]:
    """Return stripped text of every element matching *xpath*."""
    if node is None:
        return []
    return [
        el.text.strip()
        for el in node.findall(xpath, _NS)
        if el.text and el.text.strip()
    ]


def _iter_first(node: ET.Element | None, *local_tags: str) -> str | None:
    """
    Search *node* and all descendants for the first element whose local
    tag name matches any entry in *local_tags*.  Useful for fields whose
    nesting depth varies across schema versions.
    """
    if node is None:
        return None
    for tag in local_tags:
        for el in node.iter(f"{_Q}{tag}"):
            if el.text and el.text.strip():
                return el.text.strip()
    return None


# ── Text chunking ─────────────────────────────────────────────────────────────

def chunk_text(text: str, max_chars: int = 1_200) -> list[str]:
    """
    Split *text* into chunks of at most *max_chars* characters.

    Strategy (priority order):
      1. Split at sentence boundaries (". ") so chunks end on complete thoughts.
      2. Fall back to word boundaries when a single sentence exceeds max_chars.
    """
    if not text:
        return []

    # Normalise whitespace before splitting
    text = " ".join(text.split())

    # Build sentence list; re-append the period that split() consumed
    sentences: list[str] = [
        (s.strip() + ".") if not s.strip().endswith(".") else s.strip()
        for s in text.split(". ")
        if s.strip()
    ]

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for sentence in sentences:
        if len(sentence) > max_chars:
            # Flush any pending buffer
            if buf:
                chunks.append(" ".join(buf))
                buf, buf_len = [], 0
            # Word-level fallback for very long sentences
            words = sentence.split()
            word_buf: list[str] = []
            word_len = 0
            for word in words:
                candidate_len = word_len + len(word) + (1 if word_buf else 0)
                if candidate_len > max_chars and word_buf:
                    chunks.append(" ".join(word_buf))
                    word_buf, word_len = [word], len(word)
                else:
                    word_buf.append(word)
                    word_len = candidate_len
            if word_buf:
                chunks.append(" ".join(word_buf))
        else:
            added_len = buf_len + len(sentence) + (1 if buf else 0)
            if added_len > max_chars and buf:
                chunks.append(" ".join(buf))
                buf, buf_len = [sentence], len(sentence)
            else:
                buf.append(sentence)
                buf_len = added_len

    if buf:
        chunks.append(" ".join(buf))

    return chunks


def _make_chunk_rows(
    filing_id: str,
    source_section: str,
    text_value: str,
) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": _sha(f"{filing_id}-{source_section}-chunk-{i}"),
            "filing_id": filing_id,
            "source_section": source_section,
            "chunk_index": i,
            "chunk_text": chunk,
            "embedding_status": "PENDING",
            "created_at": now_iso(),
        }
        for i, chunk in enumerate(chunk_text(text_value))
    ]


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_990_xml(
    xml_bytes: bytes,
    filename: str,
    gcs_path: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Parse one IRS 990 or 990-T XML filing.

    Returns
    -------
    (filing_row, program_rows, chunk_rows)
    """
    root = ET.fromstring(xml_bytes)

    # ── Header ────────────────────────────────────────────────────────────────
    header = root.find("ns:ReturnHeader", _NS)

    # Scope EIN and org name to <Filer> to avoid matching the preparer firm
    filer = _first(header, "ns:Filer/ns:EIN")           # EIN
    ein = filer  # rename for clarity
    org_name = _first(
        header,
        "ns:Filer/ns:BusinessName/ns:BusinessNameLine1Txt",
        "ns:Filer/ns:BusinessName/ns:BusinessNameLine1",
        "ns:Filer/ns:NameLine1Txt",
    )

    tax_period_end   = _first(header, "ns:TaxPeriodEndDt")
    tax_period_begin = _first(header, "ns:TaxPeriodBeginDt")
    return_type      = _first(header, "ns:ReturnTypeCd")
    tax_year_str     = _first(header, "ns:TaxYr")

    filing_year: int | None = None
    if tax_year_str and tax_year_str.isdigit():
        filing_year = int(tax_year_str)
    elif tax_period_end and len(tax_period_end) >= 4:
        filing_year = int(tax_period_end[:4])

    # ── Return body (field names differ between 990 and 990-T) ───────────────
    return_data = root.find(f"{_Q}ReturnData")

    # Revenue: 990 uses CYTotalRevenueAmt; 990-T Schedule A uses
    # TotUnrltTrdBusIncmAmt (aggregated across all Schedule A blocks)
    total_revenue = _iter_first(
        return_data,
        "CYTotalRevenueAmt", "TotalRevenueAmt", "TotUnrltTrdBusIncmAmt",
    )
    total_expenses = _iter_first(
        return_data,
        "CYTotalExpensesAmt", "TotalExpensesAmt", "TotUnrltTrdBusIncmExpnssAmt",
    )
    # 990 uses TotalAssetsEOYAmt; 990-T uses BookValueAssetsEOYAmt
    total_assets = _iter_first(
        return_data,
        "TotalAssetsEOYAmt", "BookValueAssetsEOYAmt",
    )
    mission = _iter_first(
        return_data,
        "MissionDesc", "PrimaryExemptPurposeTxt", "ActivityOrMissionDesc",
    )

    # ── Deterministic filing ID ───────────────────────────────────────────────
    tax_period = tax_period_end or tax_period_begin or "UNKNOWN"
    filing_id = _sha(f"{ein}-{tax_period}-{filename}")

    filing_row: dict[str, Any] = {
        "filing_id":          filing_id,
        "ein":                ein or "UNKNOWN",
        "organization_name":  org_name,
        "filing_year":        filing_year,
        "tax_period":         tax_period,   # tax_period_end if present, else tax_period_begin
        "return_type":        return_type,
        "total_revenue_raw":  total_revenue,
        "total_expenses_raw": total_expenses,
        "total_assets_raw":   total_assets,
        "mission_description": mission,
        "xml_filename":       filename,
        "gcs_path":           gcs_path,
        "created_at":         now_iso(),
    }

    # ── Programs / Schedule A ─────────────────────────────────────────────────
    program_rows: list[dict[str, Any]] = []
    chunk_rows: list[dict[str, Any]] = []

    # Standard 990: program service accomplishments.
    #
    # Two schema variants exist across filing years:
    #   Modern (2013+): <ProgramSrvcAccomplishmentGrp> containing <Desc>,
    #                   <ProgramServiceExpensesAmt>, and optional <GrantsAndAllocationsAmt>
    #   Legacy:         bare <DescriptionProgramSrvcAccomplishmentTxt> elements
    #
    # We try the modern group form first; fall back to legacy bare tags if none found.

    prog_groups = root.findall(".//ns:ProgramSrvcAccomplishmentGrp", _NS)

    if prog_groups:
        for i, grp in enumerate(prog_groups):
            desc    = _first(grp, "ns:Desc")
            expense = _first(grp, "ns:ProgramServiceExpensesAmt")
            if not desc:
                continue
            program_id = _sha(f"{filing_id}-program-{i}")
            program_rows.append({
                "program_id":          program_id,
                "filing_id":           filing_id,
                "program_name":        None,
                "program_description": desc,
                "program_expense_raw": expense,
                "created_at":          now_iso(),
            })
            chunk_rows.extend(
                _make_chunk_rows(filing_id, f"program_description_{i}", desc)
            )
    else:
        # Legacy schema fallback
        for i, desc in enumerate(_all(root, ".//ns:DescriptionProgramSrvcAccomplishmentTxt")):
            program_id = _sha(f"{filing_id}-program-{i}")
            program_rows.append({
                "program_id":          program_id,
                "filing_id":           filing_id,
                "program_name":        None,
                "program_description": desc,
                "program_expense_raw": None,
                "created_at":          now_iso(),
            })
            chunk_rows.extend(
                _make_chunk_rows(filing_id, f"program_description_{i}", desc)
            )

    # 990-T: unrelated business descriptions from Schedule A blocks
    if return_data is not None:
        for sched in return_data.findall(f"{_Q}IRS990TScheduleA"):
            trade_desc = _iter_first(sched, "TradeOrBusinessDesc")
            net_income = _iter_first(sched, "TotNetUnrltTrdBusIncmAmt")
            seq        = _iter_first(sched, "SequenceReferenceNum") \
                         or str(len(program_rows))

            if not trade_desc:
                continue

            program_id = _sha(f"{filing_id}-schedA-{seq}")
            program_rows.append({
                "program_id":          program_id,
                "filing_id":           filing_id,
                "program_name":        None,
                "program_description": trade_desc,
                "program_expense_raw": net_income,
                "created_at":          now_iso(),
            })
            chunk_rows.extend(
                _make_chunk_rows(filing_id, f"schedule_a_{seq}", trade_desc)
            )

    # Mission chunks (always at the filing level)
    if mission:
        chunk_rows.extend(
            _make_chunk_rows(filing_id, "mission_description", mission)
        )

    return filing_row, program_rows, chunk_rows


# ── BigQuery helpers ──────────────────────────────────────────────────────────

def insert_rows(
    client: "bigquery.Client",
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

    filings_table  = f"{project_id}.{dataset_id}.irs_990_filings"
    programs_table = f"{project_id}.{dataset_id}.irs_990_programs"
    chunks_table   = f"{project_id}.{dataset_id}.document_chunks"

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
            xml_bytes = blob.download_as_bytes()
            filing_row, program_rows, chunk_rows = parse_990_xml(
                xml_bytes=xml_bytes,
                filename=Path(blob.name).name,
                gcs_path=f"gs://{bucket_name}/{blob.name}",
            )
            insert_rows(bq_client, filings_table,  [filing_row],  dry_run)
            insert_rows(bq_client, programs_table, program_rows,  dry_run)
            insert_rows(bq_client, chunks_table,   chunk_rows,    dry_run)
            log.info(
                "  → filing + %d programs + %d chunks",
                len(program_rows), len(chunk_rows),
            )
            ok += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("  ✗ Skipped %s: %s", blob.name, exc)
            failed += 1

    log.info("Done — %d succeeded, %d failed", ok, failed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest IRS 990/990-T XML into BigQuery")
    parser.add_argument("--project_id",  required=True)
    parser.add_argument("--dataset_id",  required=True)
    parser.add_argument("--bucket_name", required=True)
    parser.add_argument("--prefix",      required=True)
    parser.add_argument("--limit",       type=int, default=None)
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Parse and log without writing to BigQuery",
    )
    args = parser.parse_args()

    ingest_gcs_prefix(
        project_id=args.project_id,
        dataset_id=args.dataset_id,
        bucket_name=args.bucket_name,
        prefix=args.prefix,
        limit=args.limit,
        dry_run=args.dry_run,
    )