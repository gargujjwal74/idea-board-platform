terraform {
  required_version = ">= 1.5"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.14" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Partial config: bucket/prefix are injected with -backend-config (see README / pipeline)
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
  default_labels = {
    project     = "idea-board"
    environment = var.name
    managed_by  = "terraform"
  }
}

# GCP needs APIs switched on; AWS has no equivalent step.
resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "container.googleapis.com",
    "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com",
    "iam.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

module "network" {
  source     = "../../modules/network/gcp"
  name       = var.name
  region     = var.region
  depends_on = [google_project_service.apis]
}

module "cluster" {
  source             = "../../modules/k8s_cluster/gcp"
  name               = var.name
  region             = var.region
  network_id         = module.network.network_id
  subnet_ids         = module.network.subnet_ids
  node_count         = var.node_count
  node_size          = var.node_size
  kubernetes_version = var.kubernetes_version
  high_availability  = var.high_availability
  api_allowed_cidrs  = var.api_allowed_cidrs
}

module "database" {
  source            = "../../modules/postgres/gcp"
  name              = var.name
  region            = var.region
  network_id        = module.network.network_id
  subnet_ids        = module.network.subnet_ids
  allowed_cidr      = module.network.cidr
  size              = var.db_size
  high_availability = var.high_availability
}
