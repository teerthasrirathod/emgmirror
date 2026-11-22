# Private network for the serverless workloads.
# - No Cloud NAT and no external IPs: nothing in this VPC can reach the internet.
# - Google APIs are reached through private.googleapis.com (Private Google Access) and a
#   private DNS zone, so Cloud Storage, Pub/Sub, Secret Manager and Cloud SQL still work.
# - Egress firewall: deny everything, then allow only Google APIs and the Cloud SQL range.
#   The GPU worker sends ALL traffic into this VPC (Direct VPC egress), so it has no internet.
resource "google_compute_network" "this" {
  project                         = var.project_id
  name                            = var.name
  auto_create_subnetworks         = false
  routing_mode                    = "REGIONAL"
  delete_default_routes_on_create = true
}

# Only route to the Google API VIPs (the default internet route was deleted above).
resource "google_compute_route" "private_google_apis" {
  project          = var.project_id
  name             = "${var.name}-private-google-apis"
  network          = google_compute_network.this.id
  dest_range       = "199.36.153.8/30"
  next_hop_gateway = "default-internet-gateway"
  priority         = 1000
  description      = "private.googleapis.com VIPs only; no general internet route exists."
}

resource "google_compute_subnetwork" "serverless" {
  project                  = var.project_id
  name                     = "${var.name}-serverless"
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true

  log_config {
    aggregation_interval = "INTERVAL_5_SEC"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

# Private services access for Cloud SQL private IP.
resource "google_compute_global_address" "psa" {
  project       = var.project_id
  name          = "${var.name}-psa"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = var.psa_prefix_length
  network       = google_compute_network.this.id
}

resource "google_service_networking_connection" "psa" {
  network                 = google_compute_network.this.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa.name]
}

# --- Egress firewall -------------------------------------------------------------------
resource "google_compute_firewall" "deny_all_egress" {
  project            = var.project_id
  name               = "${var.name}-deny-all-egress"
  network            = google_compute_network.this.id
  direction          = "EGRESS"
  priority           = 65000
  destination_ranges = ["0.0.0.0/0"]

  deny {
    protocol = "all"
  }

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_firewall" "allow_google_apis_egress" {
  project            = var.project_id
  name               = "${var.name}-allow-google-apis"
  network            = google_compute_network.this.id
  direction          = "EGRESS"
  priority           = 1000
  destination_ranges = ["199.36.153.8/30"]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_firewall" "allow_cloudsql_egress" {
  project            = var.project_id
  name               = "${var.name}-allow-cloudsql"
  network            = google_compute_network.this.id
  direction          = "EGRESS"
  priority           = 1000
  destination_ranges = ["${google_compute_global_address.psa.address}/${var.psa_prefix_length}"]

  allow {
    protocol = "tcp"
    ports    = ["5432", "3307"]
  }

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_firewall" "deny_all_ingress" {
  project       = var.project_id
  name          = "${var.name}-deny-all-ingress"
  network       = google_compute_network.this.id
  direction     = "INGRESS"
  priority      = 65000
  source_ranges = ["0.0.0.0/0"]

  deny {
    protocol = "all"
  }

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

# --- Private DNS: *.googleapis.com -> private.googleapis.com -----------------------------
resource "google_dns_managed_zone" "googleapis" {
  project     = var.project_id
  name        = "${var.name}-googleapis"
  dns_name    = "googleapis.com."
  description = "Resolve Google APIs to the private VIPs inside the VPC."
  visibility  = "private"

  private_visibility_config {
    networks {
      network_url = google_compute_network.this.id
    }
  }
}

resource "google_dns_record_set" "private_googleapis_a" {
  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis.name
  name         = "private.googleapis.com."
  type         = "A"
  ttl          = 300
  rrdatas      = ["199.36.153.8", "199.36.153.9", "199.36.153.10", "199.36.153.11"]
}

resource "google_dns_record_set" "googleapis_cname" {
  project      = var.project_id
  managed_zone = google_dns_managed_zone.googleapis.name
  name         = "*.googleapis.com."
  type         = "CNAME"
  ttl          = 300
  rrdatas      = ["private.googleapis.com."]
}
