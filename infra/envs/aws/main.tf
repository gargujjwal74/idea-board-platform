terraform {
  required_version = ">= 1.5"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.80" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Partial config: bucket/key/region are injected with -backend-config (see README / pipeline)
  backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "idea-board", environment = var.name, managed_by = "terraform" }
  }
}

module "network" {
  source = "../../modules/network/aws"
  name   = var.name
  region = var.region
}

module "cluster" {
  source             = "../../modules/k8s_cluster/aws"
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
  source            = "../../modules/postgres/aws"
  name              = var.name
  region            = var.region
  network_id        = module.network.network_id
  subnet_ids        = module.network.subnet_ids
  allowed_cidr      = module.network.cidr
  size              = var.db_size
  high_availability = var.high_availability
}
