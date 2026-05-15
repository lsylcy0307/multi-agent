terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "services" {
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "aiplatform.googleapis.com",
    "storage.googleapis.com",
    "bigquery.googleapis.com",
    "iam.googleapis.com"
  ])

  project = var.project_id
  service = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "app_repo" {
  location      = var.region
  repository_id = "agent-platform"
  format        = "DOCKER"

  depends_on = [google_project_service.services]
}

resource "google_service_account" "cloud_run_sa" {
  account_id   = "ai-cloud-run-sa"
  display_name = "Cloud Run service account for AI app"
}

resource "google_project_iam_member" "vertex_ai_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_project_iam_member" "storage_object_admin" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_project_iam_member" "bigquery_user" {
  project = var.project_id
  role    = "roles/bigquery.user"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

resource "google_storage_bucket" "documents" {
  name          = "${var.project_id}-ai-documents"
  location      = var.region
  force_destroy = true
}

resource "google_bigquery_dataset" "ai_logs" {
  dataset_id = "ai_logs"
  location   = "US"
}

resource "google_cloud_run_v2_service" "api" {
  name     = "agent-api"
  location = var.region
  deletion_protection = false

  template {
    service_account = google_service_account.cloud_run_sa.email

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app_repo.repository_id}/agent-api:latest"

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      env {
        name  = "GCP_REGION"
        value = var.region
      }
    }
  }

  depends_on = [google_project_service.services]
}

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}