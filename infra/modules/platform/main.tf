# One environment of the CT-to-3D platform (staging or prod) in one Google Cloud project.
# Everything regional is in var.region (asia-south1, Mumbai). See
# docs/ct_platform/hosting/DEPLOY_RUNBOOK.md for the order of first deployment.

locals {
  prefix      = "ct-${var.env}"
  name_prefix = "${var.project_id}-ct-${var.env}"
  labels = merge(var.labels, {
    app        = "ct-platform"
    env        = var.env
    managed-by = "terraform"
  })

  services = [
    "artifactregistry.googleapis.com",
    "cloudkms.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "containeranalysis.googleapis.com",
    "containerscanning.googleapis.com",
    "dns.googleapis.com",
    "eventarc.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "identitytoolkit.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "orgpolicy.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "serviceusage.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
    "workflowexecutions.googleapis.com",
    "workflows.googleapis.com",
  ]

  kms_keys = ["storage", "sql", "artifacts", "pubsub", "secrets", "logging", "workflows"]

  # Service agents, derived from the project number so that `terraform plan` needs no API
  # calls. They are created by google_project_service_identity / the data sources below.
  agents = {
    sql       = "service-${var.project_number}@gcp-sa-cloud-sql.iam.gserviceaccount.com"
    pubsub    = "service-${var.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com"
    secrets   = "service-${var.project_number}@gcp-sa-secretmanager.iam.gserviceaccount.com"
    artifacts = "service-${var.project_number}@gcp-sa-artifactregistry.iam.gserviceaccount.com"
    workflows = "service-${var.project_number}@gcp-sa-workflows.iam.gserviceaccount.com"
  }

  sa = module.iam.emails
  m  = module.iam.members

  common_env = {
    UPLOAD_BUCKET        = module.storage.upload_bucket
    RESULTS_BUCKET       = module.storage.results_bucket
    JOBS_TOPIC           = module.pubsub.jobs_topic_name
    GOOGLE_CLOUD_PROJECT = var.project_id
    OIDC_ISSUER          = module.identity.oidc_issuer
    OIDC_AUDIENCE        = module.identity.oidc_audience
    CT_ENV               = var.env
  }
  secret_env = {
    DATABASE_URL = module.secrets.secret_ids["database-url"]
  }
}

module "services" {
  source     = "../services"
  project_id = var.project_id
  services   = local.services
}

# ---------------------------------------------------------------- service agents --------
resource "google_project_service_identity" "agents" {
  provider = google-beta
  for_each = toset([
    "sqladmin.googleapis.com",
    "pubsub.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "workflows.googleapis.com",
  ])

  project    = var.project_id
  service    = each.value
  depends_on = [module.services]
}

# Read after the APIs are enabled (deferred to apply on a fresh project).
data "google_storage_project_service_account" "gcs" {
  project    = var.project_id
  depends_on = [module.services]
}

data "google_logging_project_cmek_settings" "logging" {
  project    = var.project_id
  depends_on = [module.services]
}

# ------------------------------------------------------------------------- keys ---------
module "kms" {
  source        = "../kms"
  project_id    = var.project_id
  location      = var.region
  key_ring_name = "${local.prefix}-keys"
  keys          = local.kms_keys
  labels        = local.labels
  encrypters = {
    storage   = ["serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"]
    sql       = ["serviceAccount:${local.agents.sql}"]
    artifacts = ["serviceAccount:${local.agents.artifacts}"]
    pubsub    = ["serviceAccount:${local.agents.pubsub}"]
    secrets   = ["serviceAccount:${local.agents.secrets}"]
    logging   = ["serviceAccount:${data.google_logging_project_cmek_settings.logging.service_account_id}"]
    workflows = ["serviceAccount:${local.agents.workflows}"]
  }
  depends_on = [google_project_service_identity.agents]
}

# ---------------------------------------------------------------- identities ------------
module "iam" {
  source              = "../iam"
  project_id          = var.project_id
  prefix              = local.prefix
  deployer_principals = var.deployer_principals
  depends_on          = [module.services]
}

# ------------------------------------------------------------------- network ------------
module "network" {
  source      = "../network"
  project_id  = var.project_id
  name        = local.prefix
  region      = var.region
  subnet_cidr = var.subnet_cidr
  depends_on  = [module.services]
}

# ------------------------------------------------------------------ registry ------------
module "artifact_registry" {
  source        = "../artifact_registry"
  project_id    = var.project_id
  location      = var.region
  repository_id = "${local.prefix}-images"
  kms_key_id    = module.kms.key_ids["artifacts"]
  writers       = [local.m.deployer]
  readers       = concat([local.m.api, local.m.worker, local.m.cleanup], var.artifact_extra_readers)
  labels        = local.labels
}

# ------------------------------------------------------------------- storage ------------
module "storage" {
  source        = "../storage"
  project_id    = var.project_id
  name_prefix   = local.name_prefix
  location      = var.region
  kms_key_id    = module.kms.key_ids["storage"]
  cors_origins  = ["https://${var.domain}"]
  force_destroy = var.force_destroy
  labels        = local.labels
  grants = [
    # API: signs upload URLs (create) and download URLs (read); deletes on "delete now".
    { bucket = "uploads", role = "roles/storage.objectAdmin", member = local.m.api },
    { bucket = "results", role = "roles/storage.objectAdmin", member = local.m.api },
    # Worker: reads the input, writes results.
    { bucket = "uploads", role = "roles/storage.objectViewer", member = local.m.worker },
    { bucket = "results", role = "roles/storage.objectUser", member = local.m.worker },
    # Cleanup: exact-time deletion and erasure verification.
    { bucket = "uploads", role = "roles/storage.objectAdmin", member = local.m.cleanup },
    { bucket = "results", role = "roles/storage.objectAdmin", member = local.m.cleanup },
    # Deployer: uploads the static viewer.
    { bucket = "viewer", role = "roles/storage.objectAdmin", member = local.m.deployer },
  ]
  depends_on = [module.kms]
}

# ------------------------------------------------------------------ database ------------
module "database" {
  source               = "../database"
  project_id           = var.project_id
  name                 = "${local.prefix}-pg"
  region               = var.region
  tier                 = var.db_tier
  availability_type    = var.db_availability_type
  backup_location      = var.region
  network_id           = module.network.network_id
  psa_connection       = module.network.psa_connection
  kms_key_id           = module.kms.key_ids["sql"]
  iam_service_accounts = { api = local.sa.api, worker = local.sa.worker, cleanup = local.sa.cleanup }
  deletion_protection  = var.deletion_protection
  labels               = local.labels
  depends_on           = [module.kms]
}

# ------------------------------------------------------------------- secrets ------------
module "secrets" {
  source     = "../secrets"
  project_id = var.project_id
  location   = var.region
  kms_key_id = module.kms.key_ids["secrets"]
  secret_ids = ["database-url"]
  labels     = local.labels
  accessors = [
    { secret = "database-url", member = local.m.api },
    { secret = "database-url", member = local.m.worker },
    { secret = "database-url", member = local.m.cleanup },
  ]
  depends_on = [module.kms]
}

# -------------------------------------------------------------------- pubsub ------------
module "pubsub" {
  source                     = "../pubsub"
  project_id                 = var.project_id
  prefix                     = local.prefix
  kms_key_id                 = module.kms.key_ids["pubsub"]
  allowed_regions            = [var.region]
  pubsub_service_agent       = local.agents.pubsub
  publishers                 = [local.m.api]
  dead_letter_publishers     = [local.m.dispatcher]
  push_endpoint              = var.worker_mode == "service" && var.deploy_workloads ? "${module.worker[0].service_uri}/pubsub" : null
  push_service_account_email = local.sa.push
  push_audience              = var.worker_mode == "service" && var.deploy_workloads ? module.worker[0].service_uri : null
  labels                     = local.labels
  depends_on                 = [module.kms]
}

# ---------------------------------------------------------------- identity --------------
module "identity" {
  source             = "../identity"
  project_id         = var.project_id
  authorized_domains = [var.domain, "${var.project_id}.firebaseapp.com"]
  allow_self_signup  = var.allow_self_signup
  depends_on         = [module.services]
}

# ------------------------------------------------------------------ workloads -----------
# deploy_workloads = false on the very first apply (before the database-url secret has a
# version and before CI has pushed images). See DEPLOY_RUNBOOK.md.
module "api" {
  count                 = var.deploy_workloads ? 1 : 0
  source                = "../api"
  project_id            = var.project_id
  name                  = "${local.prefix}-api"
  region                = var.region
  image                 = var.api_image
  service_account_email = local.sa.api
  deployer_member       = local.m.deployer
  network_id            = module.network.network_id
  subnet_id             = module.network.subnet_id
  env                   = local.common_env
  secret_env            = local.secret_env
  health_path           = var.health_path
  max_instances         = var.api_max_instances
  deletion_protection   = var.deletion_protection
  labels                = local.labels
  depends_on            = [module.secrets, module.artifact_registry]
}

module "worker" {
  count                            = var.deploy_workloads ? 1 : 0
  source                           = "../worker"
  project_id                       = var.project_id
  name                             = "${local.prefix}-worker"
  region                           = var.region
  mode                             = var.worker_mode
  launch_stage                     = var.worker_launch_stage
  image                            = var.worker_image
  service_account_email            = local.sa.worker
  dispatcher_service_account_email = local.sa.dispatcher
  push_service_account_email       = local.sa.push
  deployer_member                  = local.m.deployer
  network_id                       = module.network.network_id
  subnet_id                        = module.network.subnet_id
  jobs_topic_id                    = module.pubsub.jobs_topic_id
  dead_letter_topic_id             = module.pubsub.dead_letter_topic_id
  workflow_kms_key_id              = module.kms.key_ids["workflows"]
  timeout_seconds                  = var.worker_timeout_seconds
  max_parallel_jobs                = var.worker_max_parallel
  env                              = local.common_env
  secret_env                       = local.secret_env
  deletion_protection              = var.deletion_protection
  labels                           = local.labels
  depends_on                       = [module.secrets, module.artifact_registry]
}

module "edge" {
  count                      = var.deploy_workloads ? 1 : 0
  source                     = "../edge"
  project_id                 = var.project_id
  prefix                     = local.prefix
  region                     = var.region
  domain                     = var.domain
  api_domain                 = var.api_domain
  api_service_name           = module.api[0].name
  viewer_bucket              = module.storage.viewer_bucket
  waf_preview                = var.waf_preview
  requests_per_minute_per_ip = var.requests_per_minute_per_ip
}

# ------------------------------------------------------------- observability ------------
module "logging" {
  source                  = "../logging"
  project_id              = var.project_id
  prefix                  = local.prefix
  region                  = var.region
  kms_key_id              = module.kms.key_ids["logging"]
  security_retention_days = var.security_log_retention_days
  lock_security_bucket    = var.lock_security_logs
  depends_on              = [module.kms]
}

module "monitoring" {
  count                    = var.deploy_workloads ? 1 : 0
  source                   = "../monitoring"
  project_id               = var.project_id
  prefix                   = local.prefix
  alert_email              = var.alert_email
  api_domain               = var.api_domain
  health_path              = var.health_path
  api_service_name         = module.api[0].name
  worker_name              = module.worker[0].name
  dead_letter_subscription = module.pubsub.dead_letter_subscription
}

module "org_policy" {
  source = "../org_policy"
  enable = var.enable_org_policies
  parent = "projects/${var.project_id}"
}
