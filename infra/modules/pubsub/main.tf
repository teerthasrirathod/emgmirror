# Job queue: the API publishes {job_id, input_uri, profile} to the jobs topic.
# Messages are stored only in the India region (message storage policy) and CMEK-encrypted.
# Failed deliveries go to a dead-letter topic with a pull subscription for inspection.
resource "google_pubsub_topic" "jobs" {
  project                    = var.project_id
  name                       = "${var.prefix}-jobs"
  kms_key_name               = var.kms_key_id
  message_retention_duration = "86400s"
  labels                     = var.labels

  message_storage_policy {
    allowed_persistence_regions = var.allowed_regions
    enforce_in_transit          = true
  }
}

resource "google_pubsub_topic" "dead_letter" {
  project                    = var.project_id
  name                       = "${var.prefix}-jobs-dead-letter"
  kms_key_name               = var.kms_key_id
  message_retention_duration = "604800s"
  labels                     = var.labels

  message_storage_policy {
    allowed_persistence_regions = var.allowed_regions
    enforce_in_transit          = true
  }
}

resource "google_pubsub_subscription" "dead_letter" {
  project                    = var.project_id
  name                       = "${var.prefix}-jobs-dead-letter"
  topic                      = google_pubsub_topic.dead_letter.id
  ack_deadline_seconds       = 60
  message_retention_duration = "604800s"
  labels                     = var.labels

  expiration_policy {
    ttl = ""
  }
}

# Push subscription to the worker's POST /pubsub (used when the worker runs as a Cloud Run
# GPU *service*; in job mode the worker module's Eventarc trigger is the push path).
resource "google_pubsub_subscription" "push" {
  count = var.push_endpoint == null ? 0 : 1

  project                    = var.project_id
  name                       = "${var.prefix}-jobs-push"
  topic                      = google_pubsub_topic.jobs.id
  ack_deadline_seconds       = 600
  message_retention_duration = "86400s"
  labels                     = var.labels

  push_config {
    push_endpoint = var.push_endpoint
    oidc_token {
      service_account_email = var.push_service_account_email
      audience              = var.push_audience
    }
  }

  retry_policy {
    minimum_backoff = "30s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = var.max_delivery_attempts
  }

  expiration_policy {
    ttl = ""
  }
}

# Pub/Sub's own service agent must be able to move messages to the dead-letter topic and
# acknowledge them on the source subscription.
resource "google_pubsub_topic_iam_member" "dlq_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.dead_letter.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${var.pubsub_service_agent}"
}

resource "google_pubsub_subscription_iam_member" "push_subscriber" {
  count = var.push_endpoint == null ? 0 : 1

  project      = var.project_id
  subscription = google_pubsub_subscription.push[0].name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${var.pubsub_service_agent}"
}

resource "google_pubsub_topic_iam_member" "publishers" {
  for_each = { for i, m in var.publishers : tostring(i) => m }

  project = var.project_id
  topic   = google_pubsub_topic.jobs.name
  role    = "roles/pubsub.publisher"
  member  = each.value
}

resource "google_pubsub_topic_iam_member" "dlq_publishers" {
  for_each = { for i, m in var.dead_letter_publishers : tostring(i) => m }

  project = var.project_id
  topic   = google_pubsub_topic.dead_letter.name
  role    = "roles/pubsub.publisher"
  member  = each.value
}
