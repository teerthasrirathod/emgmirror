# One-time bootstrap of an environment project, run by the OWNER with their own login
# (gcloud auth application-default login). Creates:
#   - the APIs Terraform itself needs
#   - a CMEK-encrypted, versioned Terraform state bucket in India
#   - the GitHub Workload Identity Federation pool (no JSON keys)
#   - two Terraform service accounts:
#       tf-plan  : read-only, usable from any workflow run in the repository (PR plans)
#       tf-apply : can change infrastructure, usable only from the protected GitHub
#                  environment (manual approval)
#   - optionally (create_budget = true, run once) the credit budget and its email channel
locals {
  apis = [
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "storage.googleapis.com",
    "cloudkms.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudbilling.googleapis.com",
    "monitoring.googleapis.com",
  ]

  # Roles for tf-apply. Narrower than roles/editor or roles/owner, but projectIamAdmin and
  # serviceAccountAdmin are still powerful: this account is only usable after a human
  # approves the GitHub environment deployment.
  apply_roles = [
    "roles/artifactregistry.admin",
    "roles/cloudkms.admin",
    "roles/cloudsql.admin",
    "roles/compute.loadBalancerAdmin",
    "roles/compute.networkAdmin",
    "roles/compute.securityAdmin",
    "roles/dns.admin",
    "roles/eventarc.admin",
    "roles/iam.roleAdmin",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.serviceAccountUser",
    "roles/identityplatform.admin",
    "roles/logging.configWriter",
    "roles/monitoring.editor",
    "roles/pubsub.admin",
    "roles/resourcemanager.projectIamAdmin",
    "roles/run.admin",
    "roles/secretmanager.admin",
    "roles/servicenetworking.networksAdmin",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/storage.admin",
    "roles/workflows.admin",
  ]

  # Read-only, per service (no basic roles/viewer). If a PR plan reports a missing read
  # permission, add the matching *.viewer role here.
  plan_roles = [
    "roles/artifactregistry.reader",
    "roles/browser",
    "roles/cloudkms.viewer",
    "roles/cloudsql.viewer",
    "roles/compute.viewer",
    "roles/dns.reader",
    "roles/eventarc.viewer",
    "roles/iam.roleViewer",
    "roles/iam.securityReviewer",
    "roles/iam.serviceAccountViewer",
    "roles/iam.workloadIdentityPoolViewer",
    "roles/identityplatform.viewer",
    "roles/logging.viewer",
    "roles/monitoring.viewer",
    "roles/pubsub.viewer",
    "roles/run.viewer",
    "roles/secretmanager.viewer",
    "roles/serviceusage.serviceUsageViewer",
    "roles/storage.bucketViewer",
    "roles/workflows.viewer",
  ]

  principal_plan  = "principal://iam.googleapis.com/${module.github_oidc.pool_name}/subject/repo:${var.github_repository}:pull_request"
  principal_apply = "principal://iam.googleapis.com/${module.github_oidc.pool_name}/subject/repo:${var.github_repository}:environment:${var.github_environment}"
}

module "services" {
  source     = "../modules/services"
  project_id = var.project_id
  services   = local.apis
}

data "google_storage_project_service_account" "gcs" {
  project    = var.project_id
  depends_on = [module.services]
}

module "kms" {
  source        = "../modules/kms"
  project_id    = var.project_id
  location      = var.region
  key_ring_name = "ct-tfstate"
  keys          = ["tfstate"]
  encrypters = {
    tfstate = ["serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"]
  }
}

resource "google_storage_bucket" "state" {
  #checkov:skip=CKV_GCP_62:Access is audited by Cloud Storage Data Access audit logs (platform logging module); a usage-log bucket for one state bucket adds cost only.
  project                     = var.project_id
  name                        = "${var.project_id}-tfstate"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  encryption {
    default_kms_key_name = module.kms.key_ids["tfstate"]
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 30
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [module.kms]
}

module "github_oidc" {
  source     = "../modules/github_oidc"
  project_id = var.project_id
  repository = var.github_repository
  depends_on = [module.services]
}

resource "google_service_account" "tf" {
  for_each = {
    plan  = "Terraform plan (read-only, PRs)"
    apply = "Terraform apply (approved GitHub environment only)"
  }

  project      = var.project_id
  account_id   = "tf-${each.key}"
  display_name = each.value
  depends_on   = [module.services]
}

resource "google_project_iam_member" "apply" {
  #checkov:skip=CKV_GCP_41:tf-apply must attach runtime service accounts to Cloud Run; usable only after manual approval of the GitHub environment.
  #checkov:skip=CKV_GCP_49:tf-apply creates the runtime service accounts; same approval gate. No keys can be created (org policy suggestion + alert).
  for_each = toset(local.apply_roles)

  project = var.project_id
  role    = each.value
  member  = google_service_account.tf["apply"].member
}

resource "google_project_iam_member" "plan" {
  for_each = toset(local.plan_roles)

  project = var.project_id
  role    = each.value
  member  = google_service_account.tf["plan"].member
}

# State: apply reads and writes; plan reads and takes the state lock.
resource "google_storage_bucket_iam_member" "state" {
  for_each = {
    apply = "roles/storage.objectAdmin"
    plan  = "roles/storage.objectUser"
  }

  bucket = google_storage_bucket.state.name
  role   = each.value
  member = google_service_account.tf[each.key].member
}

resource "google_service_account_iam_member" "plan_wif" {
  service_account_id = google_service_account.tf["plan"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.principal_plan
}

resource "google_service_account_iam_member" "apply_wif" {
  service_account_id = google_service_account.tf["apply"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.principal_apply
}

# --------------------------------------------------------------- budget (once) ---------
resource "google_monitoring_notification_channel" "budget" {
  count = var.create_budget ? 1 : 0

  project      = var.project_id
  display_name = "Budget alerts (owner)"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
  depends_on = [module.services]
}

module "budget" {
  count  = var.create_budget ? 1 : 0
  source = "../modules/budget"

  billing_account       = var.billing_account
  project_numbers       = var.budget_project_numbers
  currency_code         = var.budget_currency
  amount                = var.budget_amount
  notification_channels = [google_monitoring_notification_channel.budget[0].id]
  depends_on            = [module.services]
}
