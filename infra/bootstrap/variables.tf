variable "project_id" {
  description = "Environment project id (run bootstrap once per project)."
  type        = string
}

variable "region" {
  description = "Region for the state bucket and key."
  type        = string
  default     = "asia-south1"
}

variable "github_repository" {
  description = "owner/name of the repository allowed to federate."
  type        = string
  default     = "teerthasrirathod/emgmirror"
}

variable "github_environment" {
  description = "Protected GitHub environment allowed to use tf-apply (staging or production)."
  type        = string
}

variable "create_budget" {
  description = "Create the credit budget (run once, in one of the projects)."
  type        = bool
  default     = false
}

variable "billing_account" {
  description = "Billing account id (only when create_budget = true)."
  type        = string
  default     = ""
}

variable "budget_project_numbers" {
  description = "Project numbers covered by the budget (staging and prod)."
  type        = list(string)
  default     = []
}

variable "budget_currency" {
  description = "Billing account currency."
  type        = string
  default     = "INR"
}

variable "budget_amount" {
  description = "Credit amount."
  type        = number
  default     = 30000
}

variable "alert_email" {
  description = "Budget alert email (only when create_budget = true)."
  type        = string
  default     = ""
  sensitive   = true
}
