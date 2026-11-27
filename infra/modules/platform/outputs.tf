output "runtime_env" {
  description = "Environment variables the containers receive (RUNTIME_CONTRACT.md)."
  value       = local.common_env
}

output "service_accounts" {
  description = "Service account emails."
  value       = module.iam.emails
}

output "artifact_repository" {
  description = "Docker repository URL."
  value       = module.artifact_registry.repository_url
}

output "viewer_bucket" {
  description = "Viewer bucket."
  value       = module.storage.viewer_bucket
}

output "database_connection_name" {
  description = "Cloud SQL connection name."
  value       = module.database.connection_name
}

output "database_private_ip" {
  description = "Cloud SQL private IP (for DATABASE_URL)."
  value       = module.database.private_ip
}

output "lb_ip" {
  description = "Load balancer IP for the DNS A records."
  value       = var.deploy_workloads ? module.edge[0].lb_ip : null
}

output "url_map" {
  description = "URL map name for CDN invalidation."
  value       = var.deploy_workloads ? module.edge[0].url_map_name : null
}

output "api_service" {
  description = "Cloud Run API service name."
  value       = var.deploy_workloads ? module.api[0].name : null
}

output "worker_name" {
  description = "GPU worker job/service name."
  value       = var.deploy_workloads ? module.worker[0].name : null
}

output "notification_channel" {
  description = "Owner email notification channel (for the budget)."
  value       = var.deploy_workloads ? module.monitoring[0].notification_channel : null
}
