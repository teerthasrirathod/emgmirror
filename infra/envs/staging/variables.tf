variable "project_id" {
  description = "Google Cloud project id."
  type        = string
}

variable "project_number" {
  description = "Google Cloud project number."
  type        = string
}

variable "region" {
  description = "Region (India)."
  type        = string
  default     = "asia-south1"
}

variable "domain" {
  description = "Viewer host name."
  type        = string
}

variable "api_domain" {
  description = "API host name."
  type        = string
}

variable "alert_email" {
  description = "Alert recipient."
  type        = string
  sensitive   = true
}

variable "wif_pool_name" {
  description = "Workload identity pool resource name (output of infra/bootstrap)."
  type        = string
}

variable "github_repository" {
  description = "owner/name of the GitHub repository."
  type        = string
  default     = "teerthasrirathod/emgmirror"
}

variable "prod_deployer_email" {
  description = "Prod deployer service account (ct-prod-deployer@PROD.iam.gserviceaccount.com), once prod exists."
  type        = string
  default     = ""
}

variable "deploy_workloads" {
  description = "false on the first apply only (see DEPLOY_RUNBOOK.md)."
  type        = bool
  default     = true
}

variable "worker_mode" {
  description = "job or service."
  type        = string
  default     = "job"
}

variable "worker_launch_stage" {
  description = "GA, or BETA while GPU in the region is in preview."
  type        = string
  default     = "GA"
}

variable "enable_org_policies" {
  description = "Apply org policy suggestions (needs an organization)."
  type        = bool
  default     = false
}
