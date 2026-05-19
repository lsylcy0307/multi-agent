resource "google_bigquery_table" "irs_990_filings" {
  dataset_id = google_bigquery_dataset.query_dataset.dataset_id
  table_id   = "irs_990_filings"

  schema = jsonencode([
    {
      name = "filing_id"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "ein"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "organization_name"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "filing_year"
      type = "INTEGER"
      mode = "NULLABLE"
    },
    {
      name = "tax_period"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "return_type"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "total_revenue_raw"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "total_expenses_raw"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "total_assets_raw"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "mission_description"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "xml_filename"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "gcs_path"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "created_at"
      type = "TIMESTAMP"
      mode = "REQUIRED"
    }
  ])
}

resource "google_bigquery_table" "irs_990_programs" {
  dataset_id = google_bigquery_dataset.query_dataset.dataset_id
  table_id   = "irs_990_programs"

  schema = jsonencode([
    {
      name = "program_id"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "filing_id"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "program_name"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "program_description"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "program_expense_raw"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "created_at"
      type = "TIMESTAMP"
      mode = "REQUIRED"
    }
  ])
}

resource "google_bigquery_table" "document_chunks" {
  dataset_id = google_bigquery_dataset.query_dataset.dataset_id
  table_id   = "document_chunks"

  schema = jsonencode([
    {
      name = "chunk_id"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "filing_id"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "source_section"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "chunk_index"
      type = "INTEGER"
      mode = "REQUIRED"
    },
    {
      name = "chunk_text"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "embedding_status"
      type = "STRING"
      mode = "NULLABLE"
    },
    {
      name = "created_at"
      type = "TIMESTAMP"
      mode = "REQUIRED"
    }
  ])
}

resource "google_bigquery_table" "ingestion_jobs" {
  dataset_id = google_bigquery_dataset.query_dataset.dataset_id
  table_id   = "ingestion_jobs"

  schema = jsonencode([
    {
      name = "job_id"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "source_type"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "status"
      type = "STRING"
      mode = "REQUIRED"
    },
    {
      name = "started_at"
      type = "TIMESTAMP"
      mode = "REQUIRED"
    },
    {
      name = "completed_at"
      type = "TIMESTAMP"
      mode = "NULLABLE"
    },
    {
      name = "error_message"
      type = "STRING"
      mode = "NULLABLE"
    }
  ])
}