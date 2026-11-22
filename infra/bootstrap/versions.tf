terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.12"
    }
  }

  # Bootstrap state is local on the owner's machine the first time, then migrated into the
  # bucket it creates (see DEPLOY_RUNBOOK.md, step B4).
}

provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}
