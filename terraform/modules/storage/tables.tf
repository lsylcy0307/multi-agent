resource "google_bigquery_table" "pf_foundations" {
  dataset_id          = google_bigquery_dataset.query_dataset.dataset_id
  table_id            = "pf_foundations"
  deletion_protection = false

  schema = jsonencode([
    # ── Identity ──────────────────────────────────────────────────────────────
    { name = "foundation_id", type = "STRING", mode = "REQUIRED",
    description = "SHA-256 of ein+tax_period+filename — stable across re-ingests" },
    { name = "ein", type = "STRING", mode = "REQUIRED" },
    { name = "organization_name", type = "STRING", mode = "NULLABLE" },

    # ── Time ──────────────────────────────────────────────────────────────────
    { name = "filing_year", type = "INTEGER", mode = "NULLABLE" },
    { name = "tax_period", type = "STRING", mode = "NULLABLE" },

    # ── Location ──────────────────────────────────────────────────────────────
    { name = "state", type = "STRING", mode = "NULLABLE",
    description = "State where the foundation is registered (OrgReportOrRegisterStateCd)" },

    # ── Financials ────────────────────────────────────────────────────────────
    { name = "fmv_assets_raw", type = "STRING", mode = "NULLABLE",
    description = "Fair market value of assets EOY — better than book value for sizing grantmaking capacity" },
    { name = "total_revenue_raw", type = "STRING", mode = "NULLABLE" },
    { name = "total_expenses_raw", type = "STRING", mode = "NULLABLE" },
    { name = "total_grants_paid_raw", type = "STRING", mode = "NULLABLE",
    description = "Total contributions paid during the year" },

    # ── Application eligibility ───────────────────────────────────────────────
    { name = "accepts_unsolicited_apps", type = "BOOLEAN", mode = "NULLABLE",
    description = "false = invitation-only (OnlyContriToPreselectedInd=X), true = open to proposals, null = not specified" },

    # ── Source tracking ───────────────────────────────────────────────────────
    { name = "xml_filename", type = "STRING", mode = "NULLABLE" },
    { name = "gcs_path", type = "STRING", mode = "NULLABLE" },
    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

resource "google_bigquery_table" "pf_grants" {
  dataset_id          = google_bigquery_dataset.query_dataset.dataset_id
  table_id            = "pf_grants"
  deletion_protection = false

  schema = jsonencode([
    # ── Identity ──────────────────────────────────────────────────────────────
    { name = "grant_id", type = "STRING", mode = "REQUIRED",
    description = "SHA-256 of foundation_id+index — also used as Pinecone vector ID" },
    { name = "foundation_id", type = "STRING", mode = "REQUIRED",
    description = "FK to pf_foundations.foundation_id" },

    # ── Filer (the foundation making the grant) ───────────────────────────────
    { name = "filer_ein", type = "STRING", mode = "NULLABLE" },
    { name = "filer_name", type = "STRING", mode = "NULLABLE" },
    { name = "filer_state", type = "STRING", mode = "NULLABLE" },

    # ── Grantee (who received the money) ─────────────────────────────────────
    { name = "grantee_name", type = "STRING", mode = "NULLABLE" },
    { name = "grantee_city", type = "STRING", mode = "NULLABLE" },
    { name = "grantee_state", type = "STRING", mode = "NULLABLE" },

    # ── Grant details ─────────────────────────────────────────────────────────
    { name = "grant_amount_raw", type = "STRING", mode = "NULLABLE" },
    { name = "grant_purpose", type = "STRING", mode = "NULLABLE",
    description = "Raw purpose text from the filing — may be generic boilerplate" },

    # ── Embedding pipeline ────────────────────────────────────────────────────
    # Step 1 — built at parse time by ingest_990pf.py
    # Generic purposes stripped. Format: "Foundation (state) | Grantee (state) | purpose"
    { name = "embed_text", type = "STRING", mode = "NULLABLE",
    description = "Base embed text built at parse time. Generic purposes stripped. Used as fallback when enriched_embed_text is null." },

    # Step 2 — built by enrich_990pf.py using Claude
    # Claude generates a specific one-sentence description of what the grantee
    # does based on name, location, and purpose — replacing vague boilerplate
    # with semantically rich text. Stored here because it is expensive to
    # regenerate (API cost) and non-deterministic across runs.
    # Format: "Foundation (state) | Grantee (state) | Claude description"
    { name = "enriched_embed_text", type = "STRING", mode = "NULLABLE",
    description = "LLM-enriched embed text from enrich_990pf.py. Used by embed script when available, falls back to embed_text when null." },

    # Step 3 — set by embed_990pf.py after upserting to Pinecone
    { name = "embedding_status", type = "STRING", mode = "NULLABLE",
    description = "PENDING → COMPLETED once vector upserted to Pinecone. Embed script filters WHERE embedding_status = PENDING." },

    { name = "created_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}
