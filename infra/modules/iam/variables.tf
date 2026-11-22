variable "project_id" {
  description = "Project for the service accounts."
  type        = string
}

variable "prefix" {
  description = "Short name prefix, e.g. ct-stg (account ids must be 6-30 chars)."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,17}$", var.prefix))
    error_message = "prefix must be 3-18 lowercase letters, digits or dashes."
  }
}

variable "deployer_principals" {
  description = "Workload Identity Federation principals allowed to impersonate the deployer."
  type        = list(string)
  default     = []
}
