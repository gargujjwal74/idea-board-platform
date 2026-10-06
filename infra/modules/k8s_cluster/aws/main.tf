terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
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
  type    = string
  default = "1.31"
}
variable "api_allowed_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "CIDRs allowed to reach the public Kubernetes API (IAM-authenticated). Default is open so GitHub-hosted runners can deploy; restrict if you use self-hosted runners / static egress."
}
variable "high_availability" {
  type    = bool
  default = false
}

locals {
  # Abstract size -> AWS instance type. This map is the only AWS-specific sizing knowledge.
  instance_types = {
    small  = "t3.medium"
    medium = "t3.large"
    large  = "t3.xlarge"
  }
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.31"

  cluster_name    = var.name
  cluster_version = var.kubernetes_version

  vpc_id     = var.network_id
  subnet_ids = var.subnet_ids

  # Public API endpoint (IAM-authenticated) so CI runners can deploy. Restrict CIDRs if you have static egress IPs.
  cluster_endpoint_public_access           = true
  cluster_endpoint_public_access_cidrs     = var.api_allowed_cidrs
  enable_cluster_creator_admin_permissions = true

  cluster_addons = {
    coredns    = {}
    kube-proxy = {}
    vpc-cni    = { before_compute = true }
  }

  eks_managed_node_groups = {
    default = {
      ami_type       = "AL2023_x86_64_STANDARD"
      instance_types = [local.instance_types[var.node_size]]
      min_size       = var.node_count
      max_size       = var.node_count + 2
      desired_size   = var.node_count
    }
  }
}

# ---- Contract outputs ----
output "cluster_name" { value = module.eks.cluster_name }
output "endpoint" { value = module.eks.cluster_endpoint }
output "ca_certificate" { value = module.eks.cluster_certificate_authority_data }
output "location" { value = var.region }
