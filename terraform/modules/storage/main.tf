resource "google_storage_bucket" "documents" {
  name          = "${var.project_id}-ai-documents"
  location      = var.region
  force_destroy = true
}

resource "google_bigquery_dataset" "ai_logs" {
  dataset_id = "ai_logs"
  location   = "US"
}