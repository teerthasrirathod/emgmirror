# Logs stay in India and security logs are kept 180 days (CERT-In Directions of 28 April
# 2022: ICT system logs kept for 180 days, within Indian jurisdiction).
#   security bucket: audit logs (admin + data access), load balancer and Cloud Armor logs,
#                    VPC flow and firewall logs, Identity Platform logs. CMEK, 180+ days.
#   app bucket:      everything else (application logs carry job IDs only), 30 days.
# Data Access audit logs are turned on for the services that touch CT data or keys.
resource "google_logging_project_bucket_config" "security" {
  project        = var.project_id
  location       = var.region
  bucket_id      = "${var.prefix}-security"
  retention_days = var.security_retention_days
  locked         = var.lock_security_bucket
  description    = "Audit and security logs, 180 days (CERT-In)."

  cmek_settings {
    kms_key_name = var.kms_key_id
  }
}

resource "google_logging_project_bucket_config" "app" {
  project        = var.project_id
  location       = var.region
  bucket_id      = "${var.prefix}-app"
  retention_days = var.app_retention_days
  description    = "Application logs (job IDs only, no personal data)."

  cmek_settings {
    kms_key_name = var.kms_key_id
  }
}

locals {
  security_filter = join(" OR ", [
    "logName:\"cloudaudit.googleapis.com\"",
    "resource.type=\"http_load_balancer\"",
    "resource.type=\"network_security_policy\"",
    "logName:\"compute.googleapis.com%2Fvpc_flows\"",
    "logName:\"compute.googleapis.com%2Ffirewall\"",
    "resource.type=\"identitytoolkit_project\"",
    "protoPayload.serviceName=\"identitytoolkit.googleapis.com\"",
  ])
}

resource "google_logging_project_sink" "security" {
  project                = var.project_id
  name                   = "${var.prefix}-security"
  destination            = "logging.googleapis.com/${google_logging_project_bucket_config.security.id}"
  filter                 = local.security_filter
  unique_writer_identity = true
}

resource "google_logging_project_sink" "app" {
  project                = var.project_id
  name                   = "${var.prefix}-app"
  destination            = "logging.googleapis.com/${google_logging_project_bucket_config.app.id}"
  filter                 = "NOT (${local.security_filter})"
  unique_writer_identity = true
}

resource "google_project_iam_audit_config" "data_access" {
  for_each = toset(var.data_access_audit_services)

  project = var.project_id
  service = each.value

  audit_log_config {
    log_type = "ADMIN_READ"
  }
  audit_log_config {
    log_type = "DATA_READ"
  }
  audit_log_config {
    log_type = "DATA_WRITE"
  }
}
