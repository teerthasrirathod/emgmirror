output "name" {
  description = "Job or service name."
  value       = var.name
}

output "service_uri" {
  description = "Service URI in service mode (null in job mode)."
  value       = local.is_service ? google_cloud_run_v2_service.this[0].uri : null
}
