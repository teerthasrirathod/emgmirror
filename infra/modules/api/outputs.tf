output "name" {
  description = "Service name."
  value       = google_cloud_run_v2_service.this.name
}

output "uri" {
  description = "Service run.app URI (not reachable from the internet; ingress is LB only)."
  value       = google_cloud_run_v2_service.this.uri
}
