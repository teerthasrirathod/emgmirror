output "repository_url" {
  description = "Docker repository URL, e.g. asia-south1-docker.pkg.dev/PROJECT/REPO."
  value       = "${var.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.this.repository_id}"
}
