# Organization policy SUGGESTIONS (not applied unless enable = true and the project sits in
# an organization; a project under a personal Gmail account has no organization). Setting
# them needs roles/orgpolicy.policyAdmin at organization level, which the owner grants.
#
# Note: global resources (load balancer, Cloud Armor, Identity Platform, IAM) are not
# restricted by gcp.resourceLocations; regional data resources are.
locals {
  boolean_constraints = [
    "iam.disableServiceAccountKeyCreation",
    "iam.disableServiceAccountKeyUpload",
    "iam.automaticIamGrantsForDefaultServiceAccounts",
    "storage.uniformBucketLevelAccess",
    "sql.restrictPublicIp",
    "compute.requireOsLogin",
    "compute.skipDefaultNetworkCreation",
    "compute.disableSerialPortAccess",
  ]
}

resource "google_org_policy_policy" "resource_locations" {
  count = var.enable ? 1 : 0

  name   = "${var.parent}/policies/gcp.resourceLocations"
  parent = var.parent

  spec {
    rules {
      values {
        allowed_values = var.allowed_location_groups
      }
    }
  }
}

resource "google_org_policy_policy" "boolean" {
  for_each = var.enable ? toset(local.boolean_constraints) : toset([])

  name   = "${var.parent}/policies/${each.value}"
  parent = var.parent

  spec {
    rules {
      enforce = "TRUE"
    }
  }
}

resource "google_org_policy_policy" "no_vm_external_ip" {
  count = var.enable ? 1 : 0

  name   = "${var.parent}/policies/compute.vmExternalIpAccess"
  parent = var.parent

  spec {
    rules {
      deny_all = "TRUE"
    }
  }
}

resource "google_org_policy_policy" "run_ingress" {
  count = var.enable ? 1 : 0

  name   = "${var.parent}/policies/run.allowedIngress"
  parent = var.parent

  spec {
    rules {
      values {
        allowed_values = ["is:internal-and-cloud-load-balancing", "is:internal"]
      }
    }
  }
}
