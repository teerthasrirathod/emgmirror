output "security_bucket" {
  description = "Security log bucket id."
  value       = google_logging_project_bucket_config.security.id
}
