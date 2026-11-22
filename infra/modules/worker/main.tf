# GPU worker. Default mode "job" (see docs/ct_platform/hosting/GPU_DECISION.md):
#   Pub/Sub jobs topic --Eventarc push--> Workflows "dispatch" --> Cloud Run *job* with one
#   NVIDIA L4, one task per CT, args overridden with --job-id.
# Mode "service": a Cloud Run GPU *service* that accepts Pub/Sub push at POST /pubsub, for
# when L4 services become available in the region (the pubsub module adds the push
# subscription with dead-letter).
#
# Either way the worker has NO internet: Direct VPC egress sends all traffic into a VPC
# that has no NAT and an egress deny-all firewall (Google APIs and Cloud SQL only).

locals {
  is_job     = var.mode == "job"
  is_service = var.mode == "service"

  container_env = merge(var.env, {})
}

# ---------------------------------------------------------------- job mode -------------
resource "google_cloud_run_v2_job" "this" {
  count = local.is_job ? 1 : 0

  project             = var.project_id
  name                = var.name
  location            = var.region
  launch_stage        = var.launch_stage
  deletion_protection = var.deletion_protection
  labels              = var.labels

  template {
    task_count  = 1
    parallelism = 1
    labels      = var.labels

    template {
      service_account               = var.service_account_email
      execution_environment         = "EXECUTION_ENVIRONMENT_GEN2"
      timeout                       = "${var.timeout_seconds}s"
      max_retries                   = var.max_retries
      gpu_zonal_redundancy_disabled = true

      node_selector {
        accelerator = var.gpu_type
      }

      vpc_access {
        egress = "ALL_TRAFFIC"
        network_interfaces {
          network    = var.network_id
          subnetwork = var.subnet_id
          tags       = ["ct-worker"]
        }
      }

      containers {
        image   = var.image
        command = var.command
        args    = ["run", "--job-id", "none"]

        resources {
          limits = {
            cpu              = var.cpu
            memory           = var.memory
            "nvidia.com/gpu" = "1"
          }
        }

        dynamic "env" {
          for_each = local.container_env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = var.secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = env.value
                version = "latest"
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}

resource "google_cloud_run_v2_job_iam_member" "deployer" {
  count = local.is_job ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.this[0].name
  role     = "roles/run.developer"
  member   = var.deployer_member
}

# The dispatcher may start executions of this one job with overridden args, nothing else.
resource "google_cloud_run_v2_job_iam_member" "dispatcher" {
  count = local.is_job ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.this[0].name
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${var.dispatcher_service_account_email}"
}

resource "google_workflows_workflow" "dispatch" {
  count = local.is_job ? 1 : 0

  project             = var.project_id
  name                = "${var.name}-dispatch"
  region              = var.region
  description         = "Dispatch a CT job from Pub/Sub to the GPU Cloud Run job."
  service_account     = var.dispatcher_service_account_email
  call_log_level      = "LOG_ERRORS_ONLY"
  crypto_key_name     = var.workflow_kms_key_id
  deletion_protection = false
  labels              = var.labels
  source_contents = templatefile("${path.module}/dispatch.workflow.yaml.tftpl", {
    project_id        = var.project_id
    region            = var.region
    job_name          = var.name
    timeout_seconds   = var.timeout_seconds + 600
    dead_letter_topic = var.dead_letter_topic_id
  })
}

resource "google_eventarc_trigger" "dispatch" {
  count = local.is_job ? 1 : 0

  project         = var.project_id
  name            = "${var.name}-dispatch"
  location        = var.region
  service_account = var.dispatcher_service_account_email
  labels          = var.labels

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.pubsub.topic.v1.messagePublished"
  }

  transport {
    pubsub {
      topic = var.jobs_topic_id
    }
  }

  destination {
    workflow = google_workflows_workflow.dispatch[0].id
  }
}

# ------------------------------------------------------------ service mode -------------
resource "google_cloud_run_v2_service" "this" {
  count = local.is_service ? 1 : 0

  project             = var.project_id
  name                = var.name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  launch_stage        = var.launch_stage
  deletion_protection = var.deletion_protection
  labels              = var.labels

  template {
    service_account                  = var.service_account_email
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    timeout                          = "${var.timeout_seconds}s"
    max_instance_request_concurrency = 1
    gpu_zonal_redundancy_disabled    = true

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_parallel_jobs
    }

    node_selector {
      accelerator = var.gpu_type
    }

    vpc_access {
      egress = "ALL_TRAFFIC"
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
        tags       = ["ct-worker"]
      }
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu              = var.cpu
          memory           = var.memory
          "nvidia.com/gpu" = "1"
        }
        cpu_idle = false
      }

      dynamic "env" {
        for_each = local.container_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }
}

resource "google_cloud_run_v2_service_iam_member" "push_invoker" {
  count = local.is_service ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.push_service_account_email}"
}

resource "google_cloud_run_v2_service_iam_member" "deployer" {
  count = local.is_service ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this[0].name
  role     = "roles/run.developer"
  member   = var.deployer_member
}
