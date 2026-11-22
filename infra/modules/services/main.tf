# Enables the Google Cloud APIs a project needs. APIs are never disabled on destroy, so a
# teardown of one module cannot break another that shares the project.
resource "google_project_service" "this" {
  for_each = toset(var.services)

  project                    = var.project_id
  service                    = each.value
  disable_on_destroy         = false
  disable_dependent_services = false
}
