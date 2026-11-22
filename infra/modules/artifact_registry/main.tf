# Docker repository in the India region, CMEK-encrypted, immutable tags, vulnerability
# scanning on. Release tags (v*) are kept forever for the design history file.
resource "google_artifact_registry_repository" "this" {
  project       = var.project_id
  location      = var.location
  repository_id = var.repository_id
  description   = "CT platform images (api, worker). Deploy by digest only."
  format        = "DOCKER"
  kms_key_name  = var.kms_key_id
  labels        = var.labels

  docker_config {
    immutable_tags = true
  }

  vulnerability_scanning_config {
    enablement_config = "INHERITED"
  }

  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-releases"
    action = "KEEP"
    condition {
      tag_state    = "TAGGED"
      tag_prefixes = ["v"]
    }
  }

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 20
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 days
    }
  }
}

resource "google_artifact_registry_repository_iam_member" "writer" {
  for_each = { for i, m in var.writers : tostring(i) => m }

  project    = var.project_id
  location   = var.location
  repository = google_artifact_registry_repository.this.name
  role       = "roles/artifactregistry.writer"
  member     = each.value
}

resource "google_artifact_registry_repository_iam_member" "reader" {
  for_each = { for i, m in var.readers : tostring(i) => m }

  project    = var.project_id
  location   = var.location
  repository = google_artifact_registry_repository.this.name
  role       = "roles/artifactregistry.reader"
  member     = each.value
}
