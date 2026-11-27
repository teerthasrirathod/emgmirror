# Runtime and automation service accounts. Only project-level roles that cannot be scoped
# to a single resource are granted here; resource-level grants (bucket, topic, secret,
# Cloud Run service, repository) live next to the resource they grant on.
locals {
  accounts = {
    api        = "API service (FastAPI on Cloud Run)"
    worker     = "GPU worker (Cloud Run job)"
    deployer   = "CI deployer (images, Cloud Run revisions, viewer upload)"
    cleanup    = "Retention and erasure cleanup"
    dispatcher = "Pub/Sub to worker dispatch (Eventarc and Workflows)"
    push       = "Pub/Sub push identity (service mode only)"
  }

  # Project-level roles. Everything here is either write-only telemetry or a role that
  # Google only offers at project level (Cloud SQL client, Eventarc receiver).
  project_roles = {
    api = [
      "roles/logging.logWriter",
      "roles/monitoring.metricWriter",
      "roles/cloudtrace.agent",
      "roles/cloudsql.client",
      "roles/cloudsql.instanceUser",
    ]
    worker = [
      "roles/logging.logWriter",
      "roles/monitoring.metricWriter",
      "roles/cloudsql.client",
      "roles/cloudsql.instanceUser",
    ]
    cleanup = [
      "roles/logging.logWriter",
      "roles/cloudsql.client",
      "roles/cloudsql.instanceUser",
    ]
    dispatcher = [
      "roles/logging.logWriter",
      "roles/eventarc.eventReceiver",
      "roles/workflows.invoker",
    ]
    deployer = []
    push     = []
  }

  bindings = flatten([
    for sa, roles in local.project_roles : [for r in roles : { sa = sa, role = r }]
  ])
}

resource "google_service_account" "this" {
  for_each = local.accounts

  project      = var.project_id
  account_id   = "${var.prefix}-${each.key}"
  display_name = "${var.prefix} ${each.value}"
  description  = each.value
}

resource "google_project_iam_member" "this" {
  for_each = { for b in local.bindings : "${b.sa}|${b.role}" => b }

  project = var.project_id
  role    = each.value.role
  member  = google_service_account.this[each.value.sa].member
}

# The API signs V4 URLs for uploads and downloads with the IAM signBlob API, so it needs
# token-creator on itself only (no JSON key exists anywhere).
resource "google_service_account_iam_member" "api_self_sign" {
  service_account_id = google_service_account.this["api"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.this["api"].member
}

# The deployer may deploy Cloud Run revisions that run as the runtime accounts.
resource "google_service_account_iam_member" "deployer_act_as" {
  for_each = toset(["api", "worker", "cleanup"])

  service_account_id = google_service_account.this[each.value].name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.this["deployer"].member
}

# Custom role: invalidate the CDN cache for the viewer and nothing else.
resource "google_project_iam_custom_role" "cdn_invalidator" {
  project     = var.project_id
  role_id     = replace("${var.prefix}_cdn_invalidator", "-", "_")
  title       = "${var.prefix} CDN cache invalidator"
  description = "Invalidate Cloud CDN cache on the URL map after a viewer deploy."
  permissions = ["compute.urlMaps.get", "compute.urlMaps.invalidateCache"]
}

resource "google_project_iam_member" "deployer_cdn" {
  project = var.project_id
  role    = google_project_iam_custom_role.cdn_invalidator.id
  member  = google_service_account.this["deployer"].member
}

# GitHub Actions (Workload Identity Federation) may impersonate the deployer only from the
# protected GitHub environments of this repository. No service-account keys are created.
resource "google_service_account_iam_member" "deployer_wif" {
  for_each = toset(var.deployer_principals)

  service_account_id = google_service_account.this["deployer"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = each.value
}
