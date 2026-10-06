terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.14" }
  }
}

variable "name" { type = string }
variable "region" { type = string }
variable "network_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "node_count" { type = number }
variable "node_size" {
  type        = string
  description = "Abstract size: small | medium | large"
  validation {
    condition     = contains(["small", "medium", "large"], var.node_size)
    error_message = "node_size must be small, medium or large."
  }
}
variable "kubernetes_version" {
  type        = string
  default     = null
  description = "Optional pin (e.g. \"1.34\"). Null = the cloud's current default; old versions get retired, so pinning is opt-in"
}
variable "api_allowed_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDRs allowed to reach the public Kubernetes API (IAM-authenticated). Default is open so GitHub-hosted runners can deploy; restrict if you use self-hosted runners / static egress."
}
variable "high_availability" {
  type        = bool
  default     = false
  description = "true = regional cluster (nodes spread over 3 zones); false = single-zone (cheaper)"
}

data "google_project" "this" {}

locals {
  # Abstract size -> GCP machine type. The only GCP-specific sizing knowledge.
  machine_types = {
    small  = "e2-medium"
    medium = "e2-standard-2"
    large  = "e2-standard-4"
  }
  location = var.high_availability ? var.region : "${var.region}-a"
}

resource "google_service_account" "nodes" {
  account_id   = substr("${var.name}-nodes", 0, 30)
  display_name = "GKE nodes for ${var.name}"
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
  ])
  project = data.google_project.this.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "this" {
  name     = var.name
  location = local.location

  network    = var.network_id
  subnetwork = var.subnet_ids[0]

  min_master_version = var.kubernetes_version

  release_channel {
    channel = "REGULAR" # Google keeps the control plane on a supported version
  }

  # Managed separately so we control size/SA
  remove_default_node_pool = true
  initial_node_count       = 1
  deletion_protection      = false

  ip_allocation_policy {} # VPC-native, GKE allocates pod/service ranges

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false # public, IAM-authenticated API so CI can reach it
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.api_allowed_cidrs
      content {
        cidr_block   = cidr_blocks.value
        display_name = "allowed-${cidr_blocks.key}"
      }
    }
  }

  workload_identity_config {
    workload_pool = "${data.google_project.this.project_id}.svc.id.goog"
  }
}

resource "google_container_node_pool" "default" {
  name     = "default"
  cluster  = google_container_cluster.this.id
  location = local.location

  # node_count is per zone: keep the TOTAL equal to var.node_count for regional clusters
  node_count = var.high_availability ? max(1, ceil(var.node_count / 3)) : var.node_count

  node_config {
    machine_type    = local.machine_types[var.node_size]
    disk_size_gb    = 30
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    workload_metadata_config { mode = "GKE_METADATA" }
    metadata = {
      disable-legacy-endpoints = "true"
    }
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  depends_on = [google_project_iam_member.nodes]
}

# ---- Contract outputs ----
output "cluster_name" { value = google_container_cluster.this.name }
output "endpoint" { value = "https://${google_container_cluster.this.endpoint}" }
output "ca_certificate" { value = google_container_cluster.this.master_auth[0].cluster_ca_certificate }
output "location" { value = local.location }
