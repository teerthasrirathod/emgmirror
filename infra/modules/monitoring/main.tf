# Uptime check on the API and alerts that page the owner by email:
#   API down, API 5xx > 5 %, dead-letter backlog, failed GPU executions, Cloud SQL CPU/disk,
#   and security events (IAM policy changes, KMS key destruction, audit-log config changes)
#   that may start the CERT-In 6-hour reporting clock (SECURITY_CHECKLIST.md).
resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "${var.prefix} owner email"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

locals {
  channels = [google_monitoring_notification_channel.email.id]
}

resource "google_monitoring_uptime_check_config" "api" {
  project      = var.project_id
  display_name = "${var.prefix} API health"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path           = var.health_path
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"
    accepted_response_status_codes {
      status_class = "STATUS_CLASS_2XX"
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = var.api_domain
    }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  project      = var.project_id
  display_name = "${var.prefix} API uptime check failing"
  combiner     = "OR"

  conditions {
    display_name = "Uptime check failed"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.api.uptime_check_id}\" AND resource.type=\"uptime_url\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "600s"
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
      trigger {
        count = 1
      }
    }
  }

  notification_channels = local.channels
}

resource "google_monitoring_alert_policy" "api_5xx" {
  project      = var.project_id
  display_name = "${var.prefix} API 5xx rate above 5 %"
  combiner     = "OR"

  conditions {
    display_name = "5xx ratio"
    condition_prometheus_query_language {
      query               = "sum(rate(run_googleapis_com:request_count{monitored_resource=\"cloud_run_revision\",service_name=\"${var.api_service_name}\",response_code_class=\"5xx\"}[5m])) / sum(rate(run_googleapis_com:request_count{monitored_resource=\"cloud_run_revision\",service_name=\"${var.api_service_name}\"}[5m])) > 0.05"
      duration            = "300s"
      evaluation_interval = "60s"
    }
  }

  notification_channels = local.channels
}

resource "google_monitoring_alert_policy" "dead_letter" {
  project      = var.project_id
  display_name = "${var.prefix} job dead-lettered"
  combiner     = "OR"

  conditions {
    display_name = "Dead-letter backlog > 0"
    condition_threshold {
      filter          = "metric.type=\"pubsub.googleapis.com/subscription/num_undelivered_messages\" AND resource.type=\"pubsub_subscription\" AND resource.label.subscription_id=\"${var.dead_letter_subscription}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "60s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }

  notification_channels = local.channels
}

resource "google_monitoring_alert_policy" "worker_failed" {
  project      = var.project_id
  display_name = "${var.prefix} GPU job execution failed"
  combiner     = "OR"

  conditions {
    display_name = "Failed executions"
    condition_threshold {
      filter          = "metric.type=\"run.googleapis.com/job/completed_execution_count\" AND resource.type=\"cloud_run_job\" AND resource.label.job_name=\"${var.worker_name}\" AND metric.label.result=\"failed\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = local.channels
}

resource "google_monitoring_alert_policy" "sql" {
  project      = var.project_id
  display_name = "${var.prefix} Cloud SQL CPU or disk high"
  combiner     = "OR"

  conditions {
    display_name = "CPU > 80 %"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\" AND resource.type=\"cloudsql_database\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "900s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  conditions {
    display_name = "Disk > 80 %"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/disk/utilization\" AND resource.type=\"cloudsql_database\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "900s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  notification_channels = local.channels
}

# Security events: log-match alerts (each may be a reportable incident, see the runbook).
resource "google_monitoring_alert_policy" "security_events" {
  for_each = {
    iam_change = {
      name   = "IAM policy changed"
      filter = "protoPayload.methodName=\"SetIamPolicy\" AND logName:\"cloudaudit.googleapis.com%2Factivity\""
    }
    kms_destroy = {
      name   = "KMS key version destroyed or disabled"
      filter = "protoPayload.serviceName=\"cloudkms.googleapis.com\" AND (protoPayload.methodName:\"DestroyCryptoKeyVersion\" OR protoPayload.methodName:\"UpdateCryptoKeyVersion\")"
    }
    audit_change = {
      name   = "Log sink or bucket changed or deleted"
      filter = "protoPayload.serviceName=\"logging.googleapis.com\" AND (protoPayload.methodName:\"DeleteSink\" OR protoPayload.methodName:\"UpdateSink\" OR protoPayload.methodName:\"DeleteBucket\" OR protoPayload.methodName:\"UpdateBucket\")"
    }
    sa_key = {
      name   = "Service account key created"
      filter = "protoPayload.methodName=\"google.iam.admin.v1.CreateServiceAccountKey\""
    }
    bucket_public = {
      name   = "Bucket IAM made public"
      filter = "protoPayload.methodName=\"storage.setIamPermissions\" AND protoPayload.serviceData.policyDelta.bindingDeltas.member=(\"allUsers\" OR \"allAuthenticatedUsers\")"
    }
  }

  project      = var.project_id
  display_name = "${var.prefix} SECURITY: ${each.value.name}"
  combiner     = "OR"

  conditions {
    display_name = each.value.name
    condition_matched_log {
      filter = each.value.filter
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "604800s"
  }

  documentation {
    content   = "Possible security incident. Follow docs/ct_platform/hosting/SECURITY_CHECKLIST.md section 'Incident response' (CERT-In: report within 6 hours of noticing)."
    mime_type = "text/markdown"
  }

  notification_channels = local.channels
}
