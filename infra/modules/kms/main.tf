# Customer-managed encryption keys (CMEK), one key per data class, all in the India region.
# Keys rotate every 90 days. Destroying a key version is crypto-shredding of everything it
# protects: prevent_destroy plus the scheduled-destroy delay guard against accidents.
resource "google_kms_key_ring" "this" {
  project  = var.project_id
  name     = var.key_ring_name
  location = var.location
}

resource "google_kms_crypto_key" "this" {
  for_each = toset(var.keys)

  name                       = each.value
  key_ring                   = google_kms_key_ring.this.id
  purpose                    = "ENCRYPT_DECRYPT"
  rotation_period            = var.rotation_period
  destroy_scheduled_duration = var.destroy_scheduled_duration

  version_template {
    algorithm        = "GOOGLE_SYMMETRIC_ENCRYPTION"
    protection_level = var.protection_level
  }

  labels = var.labels

  # Key rings and keys cannot be deleted in Google Cloud; destroying versions shreds data.
  # Teardown removes keys from state instead (DEPLOY_RUNBOOK.md, Teardown).
  lifecycle {
    prevent_destroy = true
  }
}

locals {
  bindings = flatten([
    for key, members in var.encrypters : [
      for i, m in members : { key = key, idx = i, member = m }
    ]
  ])
}

resource "google_kms_crypto_key_iam_member" "encrypter" {
  for_each = { for b in local.bindings : "${b.key}|${b.idx}" => b }

  crypto_key_id = google_kms_crypto_key.this[each.value.key].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = each.value.member
}
