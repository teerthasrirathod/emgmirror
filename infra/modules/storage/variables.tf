variable "project_id" {
  description = "Project for the buckets."
  type        = string
}

variable "name_prefix" {
  description = "Globally unique bucket name prefix, e.g. <project-id>-ct-stg."
  type        = string
}

variable "location" {
  description = "Bucket location: a single India region for data residency."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK key for the uploads, results and logs buckets."
  type        = string
}

variable "upload_retention_days" {
  description = "Lifecycle backstop for uploads (the contract says 24 h)."
  type        = number
  default     = 1
}

variable "results_retention_days" {
  description = "Lifecycle backstop for results (the contract says 7 days)."
  type        = number
  default     = 7
}

variable "access_log_retention_days" {
  description = "Retention of storage access logs (CERT-In asks for 180 days of logs)."
  type        = number
  default     = 180
}

variable "cors_origins" {
  description = "Browser origins allowed to use signed URLs on the uploads/results buckets."
  type        = list(string)
}

variable "grants" {
  description = "Bucket IAM grants: bucket is uploads, results or viewer."
  type = list(object({
    bucket = string
    role   = string
    member = string
  }))
  default = []
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete non-empty buckets (staging only)."
  type        = bool
  default     = false
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
