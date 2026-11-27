# Workload Identity Federation for GitHub Actions: no JSON keys anywhere.
# Only tokens from this repository are accepted (attribute condition). Which workflow may
# impersonate which service account is decided by the principal used in each IAM binding:
#   plan  : pull-request runs of the repository (read-only service account)
#   apply : only jobs running in the protected GitHub environment "<env>" (manual approval)
resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = var.pool_id
  display_name              = "GitHub Actions"
  description               = "GitHub Actions OIDC for ${var.repository}"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
    "attribute.environment"      = "assertion.environment"
    "attribute.ref"              = "assertion.ref"
    "attribute.workflow_ref"     = "assertion.job_workflow_ref"
  }

  # Accept only: pull-request runs of this repository (read-only plan) and jobs in its
  # protected deployment environments. Pushes, forks' runs and other repos are refused.
  attribute_condition = join(" || ", concat(
    ["assertion.sub == 'repo:${var.repository}:pull_request'"],
    [for e in var.environments : "assertion.sub == 'repo:${var.repository}:environment:${e}'"],
  ))

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}
