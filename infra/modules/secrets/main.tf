# Secret Manager secret containers only. Terraform never creates secret *values*, so no
# secret ever lands in the repo or in Terraform state: the runbook adds versions with gcloud.
resource "google_secret_manager_secret" "this" {
  for_each = toset(var.secret_ids)

  project   = var.project_id
  secret_id = each.value
  labels    = var.labels

  replication {
    user_managed {
      replicas {
        location = var.location
        customer_managed_encryption {
          kms_key_name = var.kms_key_id
        }
      }
    }
  }
}

resource "google_secret_manager_secret_iam_member" "accessor" {
  for_each = { for i, g in var.accessors : "${g.secret}|${i}" => g }

  project   = var.project_id
  secret_id = google_secret_manager_secret.this[each.value.secret].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member
}
