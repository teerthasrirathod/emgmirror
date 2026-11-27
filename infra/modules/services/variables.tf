variable "project_id" {
  description = "Project to enable the APIs in."
  type        = string
}

variable "services" {
  description = "API service names, e.g. run.googleapis.com."
  type        = list(string)
}
