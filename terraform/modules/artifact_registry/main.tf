resource "google_artifact_registry_repository" "app_repo" {
  location      = var.region
  repository_id = "agent-platform"
  format        = "DOCKER"
}