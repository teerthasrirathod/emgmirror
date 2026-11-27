variable "project_id" {
  description = "Project for the repository."
  type        = string
}

variable "location" {
  description = "Repository region."
  type        = string
}

variable "repository_id" {
  description = "Repository id."
  type        = string
}

variable "kms_key_id" {
  description = "CMEK key id."
  type        = string
}

variable "writers" {
  description = "Members allowed to push images."
  type        = list(string)
  default     = []
}

variable "readers" {
  description = "Members allowed to pull images."
  type        = list(string)
  default     = []
}

variable "labels" {
  description = "Labels."
  type        = map(string)
  default     = {}
}
