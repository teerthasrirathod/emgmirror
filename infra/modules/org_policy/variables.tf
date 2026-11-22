variable "enable" {
  description = "Apply the policies (needs an organization and orgpolicy.policyAdmin)."
  type        = bool
  default     = false
}

variable "parent" {
  description = "projects/PROJECT_ID, folders/ID or organizations/ID."
  type        = string
}

variable "allowed_location_groups" {
  description = "Allowed resource locations (value groups)."
  type        = list(string)
  default     = ["in:asia-south1-locations", "in:asia-south2-locations"]
}
