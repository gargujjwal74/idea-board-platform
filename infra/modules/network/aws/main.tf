terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.80" }
  }
}

variable "name" { type = string }
variable "region" { type = string }
variable "cidr" {
  type    = string
  default = "10.10.0.0/16"
}

data "aws_availability_zones" "available" { state = "available" }

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.16"

  name = var.name
  cidr = var.cidr
  azs  = local.azs

  private_subnets = [for i, _ in local.azs : cidrsubnet(var.cidr, 4, i)]
  public_subnets  = [for i, _ in local.azs : cidrsubnet(var.cidr, 8, 200 + i)]

  enable_nat_gateway   = true
  single_nat_gateway   = true # cost: one NAT. Use one per AZ for production HA.
  enable_dns_hostnames = true

  # Lets Kubernetes place load balancers in the right subnets
  public_subnet_tags  = { "kubernetes.io/role/elb" = 1 }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = 1 }
}

# ---- Contract outputs (identical names/types across clouds) ----
output "network_id" { value = module.vpc.vpc_id }
output "subnet_ids" { value = module.vpc.private_subnets }
output "cidr" { value = var.cidr }
