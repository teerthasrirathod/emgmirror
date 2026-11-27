output "state_bucket" {
  description = "Terraform state bucket (terraform init -backend-config=bucket=...)."
  value       = google_storage_bucket.state.name
}

output "wif_pool_name" {
  description = "Pool name for envs/*/terraform.tfvars (wif_pool_name)."
  value       = module.github_oidc.pool_name
}

output "wif_provider" {
  description = "GitHub repository variable GCP_WIF_PROVIDER."
  value       = module.github_oidc.provider_name
}

output "tf_plan_service_account" {
  description = "GitHub variable GCP_TF_PLAN_SA."
  value       = google_service_account.tf["plan"].email
}

output "tf_apply_service_account" {
  description = "GitHub environment variable GCP_TF_APPLY_SA."
  value       = google_service_account.tf["apply"].email
}
