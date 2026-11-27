# Cloud SQL for PostgreSQL: private IP only, TLS enforced, CMEK, daily backups and
# point-in-time recovery in the India region, IAM database authentication for the service
# accounts. AlloyDB is not used: job records are small and Cloud SQL's smallest tiers cost
# a fraction of AlloyDB's minimum (see GPU_DECISION.md, cost section).
resource "google_sql_database_instance" "this" {
  #checkov:skip=CKV_GCP_6:TLS is mandatory (ENCRYPTED_ONLY) on a private-IP-only instance. TRUSTED_CLIENT_CERTIFICATE_REQUIRED needs the Cloud SQL connector in the app; switch once S4 uses it (DATABASE_URL contract today).
  project             = var.project_id
  name                = var.name
  region              = var.region
  database_version    = var.database_version
  encryption_key_name = var.kms_key_id
  deletion_protection = var.deletion_protection

  settings {
    tier                        = var.tier
    edition                     = "ENTERPRISE"
    availability_type           = var.availability_type
    disk_type                   = "PD_SSD"
    disk_size                   = var.disk_size_gb
    disk_autoresize             = true
    deletion_protection_enabled = var.deletion_protection
    user_labels                 = var.labels

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = var.network_id
      ssl_mode                                      = "ENCRYPTED_ONLY"
      enable_private_path_for_google_cloud_services = false
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "20:30" # 02:00 IST
      location                       = var.backup_location
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = var.retained_backups
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day          = 7  # Sunday
      hour         = 21 # 02:30 IST Monday
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled  = true
      query_string_length     = 1024
      record_application_tags = false
      record_client_address   = false
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }
    database_flags {
      name  = "log_checkpoints"
      value = "on"
    }
    database_flags {
      name  = "log_connections"
      value = "on"
    }
    database_flags {
      name  = "log_disconnections"
      value = "on"
    }
    database_flags {
      name  = "log_lock_waits"
      value = "on"
    }
    database_flags {
      name  = "log_min_messages"
      value = "error"
    }
    database_flags {
      name  = "log_min_error_statement"
      value = "error"
    }
    database_flags {
      name  = "log_min_duration_statement"
      value = "-1"
    }
    database_flags {
      name  = "log_statement"
      value = "ddl"
    }
    database_flags {
      name  = "log_duration"
      value = "on"
    }
    database_flags {
      name  = "log_hostname"
      value = "on"
    }
    database_flags {
      name  = "cloudsql.enable_pgaudit"
      value = "on"
    }
    database_flags {
      name  = "pgaudit.log"
      value = "ddl,role"
    }
  }

  depends_on = [var.psa_connection]
}

resource "google_sql_database" "this" {
  project  = var.project_id
  instance = google_sql_database_instance.this.name
  name     = var.database_name
}

# IAM database users: no passwords. The service connects as its service account.
resource "google_sql_user" "iam" {
  for_each = var.iam_service_accounts

  project  = var.project_id
  instance = google_sql_database_instance.this.name
  name     = trimsuffix(each.value, ".gserviceaccount.com")
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}
