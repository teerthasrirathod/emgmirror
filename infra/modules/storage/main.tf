# Buckets:
#   uploads  - de-identified inputs (<job_id>/input.nii.gz), deleted after 24 h
#   results  - outputs (<job_id>/...), deleted after 7 days
#   logs     - Cloud Storage usage/access logs for the two buckets above (180 days)
#   viewer   - the public static viewer (no personal data), served through Cloud CDN
#
# The PHI-adjacent buckets (uploads, results) use uniform access, public access
# prevention, CMEK, no versioning and no soft delete, so a deletion is final when the
# lifecycle rule or the "delete now" button runs. Lifecycle deletion is asynchronous
# (Google says it can lag the condition by up to 24 h): the service's own cleanup does the
# exact 24 h / 7 day delete, and these rules are the backstop.

locals {
  phi_buckets = {
    uploads = { age_days = var.upload_retention_days, cors_methods = ["PUT", "POST", "OPTIONS"] }
    results = { age_days = var.results_retention_days, cors_methods = ["GET", "HEAD", "OPTIONS"] }
  }
}

resource "google_storage_bucket" "logs" {
  #checkov:skip=CKV_GCP_62:This is the access-log sink itself; logging it to itself would loop.
  #checkov:skip=CKV_GCP_78:Log objects are write-once and expire after 180 days; versioning adds nothing.
  project                     = var.project_id
  name                        = "${var.name_prefix}-access-logs"
  location                    = var.location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.force_destroy
  labels                      = var.labels

  encryption {
    default_kms_key_name = var.kms_key_id
  }

  versioning {
    enabled = false
  }

  soft_delete_policy {
    retention_duration_seconds = 0
  }

  lifecycle_rule {
    condition {
      age = var.access_log_retention_days
    }
    action {
      type = "Delete"
    }
  }
}

# Cloud Storage writes usage logs as this Google group.
resource "google_storage_bucket_iam_member" "logs_writer" {
  bucket = google_storage_bucket.logs.name
  role   = "roles/storage.objectCreator"
  member = "group:cloud-storage-analytics@google.com"
}

resource "google_storage_bucket" "phi" {
  #checkov:skip=CKV_GCP_78:Versioning is deliberately OFF: deletion (24 h / 7 d / "delete now") must be final for DPDP erasure.
  for_each = local.phi_buckets

  project                     = var.project_id
  name                        = "${var.name_prefix}-${each.key}"
  location                    = var.location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.force_destroy
  labels                      = merge(var.labels, { data-class = "personal-health" })

  encryption {
    default_kms_key_name = var.kms_key_id
  }

  versioning {
    enabled = false
  }

  # No soft delete: erasure must be final (DPDP erasure, "delete now" receipt).
  soft_delete_policy {
    retention_duration_seconds = 0
  }

  lifecycle_rule {
    condition {
      age = each.value.age_days
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age = 1
    }
    action {
      type = "AbortIncompleteMultipartUpload"
    }
  }

  logging {
    log_bucket        = google_storage_bucket.logs.name
    log_object_prefix = each.key
  }

  cors {
    origin          = var.cors_origins
    method          = each.value.cors_methods
    response_header = ["Content-Type", "Content-MD5", "x-goog-content-length-range"]
    max_age_seconds = 3600
  }
}

# The public viewer: static files only, no personal data. Objects are readable by anyone
# (Cloud CDN backend buckets need public objects), so public access prevention is
# "inherited" here and only here. Versioning stays on so a bad deploy can be rolled back.
resource "google_storage_bucket" "viewer" {
  #checkov:skip=CKV_GCP_28:Public static site (viewer JS/CSS/HTML); contains no personal data by design.
  #checkov:skip=CKV_GCP_114:Public static site behind Cloud CDN; PHI buckets enforce public access prevention.
  project                     = var.project_id
  name                        = "${var.name_prefix}-viewer"
  location                    = var.location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "inherited"
  force_destroy               = var.force_destroy
  labels                      = merge(var.labels, { data-class = "public" })

  versioning {
    enabled = true
  }

  website {
    main_page_suffix = "index.html"
    not_found_page   = "index.html"
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 10
      with_state         = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  logging {
    log_bucket        = google_storage_bucket.logs.name
    log_object_prefix = "viewer"
  }
}

resource "google_storage_bucket_iam_member" "viewer_public" {
  #checkov:skip=CKV_GCP_28:Intentional: public static viewer served via Cloud CDN.
  bucket = google_storage_bucket.viewer.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}

# --- Resource-level grants ------------------------------------------------------------
resource "google_storage_bucket_iam_member" "grants" {
  for_each = { for i, g in var.grants : "${g.bucket}|${g.role}|${i}" => g }

  bucket = each.value.bucket == "viewer" ? google_storage_bucket.viewer.name : google_storage_bucket.phi[each.value.bucket].name
  role   = each.value.role
  member = each.value.member
}
