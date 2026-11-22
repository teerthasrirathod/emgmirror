output "platform" {
  description = "Everything CI and the runbook need."
  value = {
    runtime_env              = module.platform.runtime_env
    service_accounts         = module.platform.service_accounts
    artifact_repository      = module.platform.artifact_repository
    viewer_bucket            = module.platform.viewer_bucket
    database_connection_name = module.platform.database_connection_name
    database_private_ip      = module.platform.database_private_ip
    lb_ip                    = module.platform.lb_ip
    url_map                  = module.platform.url_map
    api_service              = module.platform.api_service
    worker_name              = module.platform.worker_name
  }
}
