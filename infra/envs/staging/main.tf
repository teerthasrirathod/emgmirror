# Staging environment. Values that identify the owner (project, domain, alert email) are
# not committed: copy terraform.tfvars.example to terraform.tfvars (git-ignored) or set
# TF_VAR_* in the CI environment.
module "platform" {
  source = "../../modules/platform"

  project_id     = var.project_id
  project_number = var.project_number
  env            = "stg"
  region         = var.region
  domain         = var.domain
  api_domain     = var.api_domain
  alert_email    = var.alert_email

  # Only GitHub jobs in the protected "staging" environment may deploy.
  deployer_principals = [
    "principal://iam.googleapis.com/${var.wif_pool_name}/subject/repo:${var.github_repository}:environment:staging",
  ]

  # The prod deployer copies the exact signed digest from staging to prod.
  artifact_extra_readers = var.prod_deployer_email == "" ? [] : ["serviceAccount:${var.prod_deployer_email}"]

  deploy_workloads     = var.deploy_workloads
  worker_mode          = var.worker_mode
  worker_launch_stage  = var.worker_launch_stage
  db_tier              = "db-f1-micro"
  db_availability_type = "ZONAL"
  api_max_instances    = 3
  waf_preview          = true
  lock_security_logs   = false
  deletion_protection  = false
  force_destroy        = true
  enable_org_policies  = var.enable_org_policies
}
