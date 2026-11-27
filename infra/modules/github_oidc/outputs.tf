output "pool_name" {
  description = "Full pool resource name (projects/NUMBER/locations/global/workloadIdentityPools/ID)."
  value       = google_iam_workload_identity_pool.github.name
}

output "provider_name" {
  description = "Provider resource name for google-github-actions/auth (workload_identity_provider)."
  value       = google_iam_workload_identity_pool_provider.github.name
}
