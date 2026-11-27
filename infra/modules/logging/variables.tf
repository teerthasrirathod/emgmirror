variable "project_id" {
  description = "Project."
  type        = string
}

variable "prefix" {
  description = "Resource name prefix."
  type        = string
}

variable "region" {
  description = "Log bucket location (India)."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK key for the log buckets (the Logging service agent needs encrypt/decrypt)."
  type        = string
}

variable "security_retention_days" {
  description = "Retention of security logs (CERT-In minimum 180)."
  type        = number
  default     = 180
  validation {
    condition     = var.security_retention_days >= 180
    error_message = "CERT-In requires at least 180 days."
  }
}

variable "app_retention_days" {
  description = "Retention of application logs."
  type        = number
  default     = 30
}

variable "lock_security_bucket" {
  description = "Lock the retention policy (irreversible). Turn on in prod after validation."
  type        = bool
  default     = false
}

variable "data_access_audit_services" {
  description = "Services with Data Access audit logs on."
  type        = list(string)
  default = [
    "storage.googleapis.com",
    "cloudsql.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudkms.googleapis.com",
    "iam.googleapis.com",
    "run.googleapis.com",
  ]
}
