terraform {
  required_version = ">= 1.5"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.80" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
}

variable "name" { type = string }
variable "region" { type = string }
variable "network_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "allowed_cidr" { type = string }
variable "size" {
  type = string
  validation {
    condition     = contains(["small", "medium", "large"], var.size)
    error_message = "size must be small, medium or large."
  }
}
variable "db_name" {
  type    = string
  default = "ideas"
}
variable "db_user" {
  type    = string
  default = "ideas"
}
variable "storage_gb" {
  type    = number
  default = 20
}
variable "high_availability" {
  type    = bool
  default = false
}
variable "deletion_protection" {
  type    = bool
  default = false
}

locals {
  instance_class = {
    small  = "db.t4g.micro"
    medium = "db.t4g.medium"
    large  = "db.m6g.large"
  }
}

resource "random_password" "db" {
  length  = 24
  special = false # keeps the connection URL free of escaping problems
}

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.subnet_ids
}

resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "Postgres access from inside the VPC only"
  vpc_id      = var.network_id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
  }
}

resource "aws_db_instance" "this" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = "16"
  instance_class = local.instance_class[var.size]

  allocated_storage = var.storage_gb
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = var.db_name
  username = var.db_user
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = var.high_availability

  backup_retention_period = var.high_availability ? 7 : 1
  deletion_protection     = var.deletion_protection
  skip_final_snapshot     = !var.deletion_protection
  apply_immediately       = true
}

# ---- Contract outputs ----
output "host" { value = aws_db_instance.this.address }
output "port" { value = aws_db_instance.this.port }
output "db_name" { value = var.db_name }
output "user" { value = var.db_user }
output "password" {
  value     = random_password.db.result
  sensitive = true
}
# RDS for PG15+ enforces TLS, so sslmode=require is mandatory
output "connection_url" {
  value     = "postgresql://${var.db_user}:${random_password.db.result}@${aws_db_instance.this.address}:${aws_db_instance.this.port}/${var.db_name}?sslmode=require"
  sensitive = true
}
