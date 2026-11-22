variable "project_id" {
  description = "Project for the network."
  type        = string
}

variable "name" {
  description = "Network name prefix."
  type        = string
}

variable "region" {
  description = "Region of the serverless subnet."
  type        = string
}

variable "subnet_cidr" {
  description = "CIDR of the subnet used by Direct VPC egress (needs room for Cloud Run instances)."
  type        = string
  default     = "10.10.0.0/24"
}

variable "psa_prefix_length" {
  description = "Prefix length of the private services access range (Cloud SQL)."
  type        = number
  default     = 20
}
