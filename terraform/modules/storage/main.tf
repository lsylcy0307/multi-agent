resource "google_storage_bucket" "documents" {
  name          = "${var.project_id}-ai-documents"
  location      = var.region
  force_destroy = true
}

resource "google_bigquery_dataset" "query_dataset" {
  dataset_id = "query_dataset"
  location   = "US"
}