# Prod environment. Values that identify the owner (project, domain, alert email) are
# not committed: copy terraform.tfvars.example to terraform.tfvars (git-ignored) or set
# TF_VAR_* in the CI environment.
module "platform" {
  source = "../../modules/platform"

  project_id     = var.project_id
  project_number = var.project_number
  env            = "prod"
  region         = var.region
  domain         = var.domain
  api_domain     = var.api_domain
  alert_email    = var.alert_email

  # Only GitHub jobs in the protected "production" environment may deploy.
  deployer_principals = [
    "principal://iam.googleapis.com/${var.wif_pool_name}/subject/repo:${var.github_repository}:environment:production",
  ]

  deploy_workloads     = var.deploy_workloads
  worker_mode          = var.worker_mode
  worker_launch_stage  = var.worker_launch_stage
  db_tier              = "db-custom-1-3840"
  db_availability_type = "REGIONAL"
  api_max_instances    = 10
  waf_preview          = false
  lock_security_logs   = true

  # CERT-In needs >= 180 days; DPDP Rules point to one year for logs (DATA_PROTECTION.md).
  security_log_retention_days = 400
  deletion_protection         = true
  force_destroy               = false
  enable_org_policies         = var.enable_org_policies
}
