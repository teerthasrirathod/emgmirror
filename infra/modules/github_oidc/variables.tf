variable "project_id" {
  description = "Project that hosts the pool."
  type        = string
}

variable "pool_id" {
  description = "Pool id."
  type        = string
  default     = "github"
}

variable "environments" {
  description = "GitHub deployment environments allowed to federate."
  type        = list(string)
  default     = ["staging", "production"]
}

variable "repository" {
  description = "owner/name of the GitHub repository."
  type        = string
}
