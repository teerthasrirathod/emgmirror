variable "project_id" {
  description = "Project for the instance."
  type        = string
}

variable "name" {
  description = "Instance name."
  type        = string
}

variable "region" {
  description = "Instance region."
  type        = string
}

variable "database_version" {
  description = "PostgreSQL major version."
  type        = string
  default     = "POSTGRES_18"
}

variable "tier" {
  description = "Machine tier, e.g. db-f1-micro (staging) or db-custom-1-3840 (prod)."
  type        = string
}

variable "availability_type" {
  description = "ZONAL or REGIONAL (high availability)."
  type        = string
  default     = "ZONAL"
}

variable "disk_size_gb" {
  description = "Initial disk size (auto-resizes)."
  type        = number
  default     = 10
}

variable "backup_location" {
  description = "Backup location (keep in India)."
  type        = string
}

variable "retained_backups" {
  description = "Number of daily backups kept."
  type        = number
  default     = 14
}

variable "network_id" {
  description = "VPC id for private IP."
  type        = string
}

variable "psa_connection" {
  description = "Private services access connection id (dependency only)."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK key."
  type        = string
}

variable "database_name" {
  description = "Application database name."
  type        = string
  default     = "ctservice"
}

variable "iam_service_accounts" {
  description = "Map of static key => service account email that gets an IAM database user."
  type        = map(string)
  default     = {}
}

variable "deletion_protection" {
  description = "Protect the instance from deletion."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
