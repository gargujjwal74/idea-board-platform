# Identical on every cloud: the pipeline reads only these.
output "cloud" { value = "aws" }
output "region" { value = var.region }
output "cluster_name" { value = module.cluster.cluster_name }
output "cluster_location" { value = module.cluster.location }
output "cluster_endpoint" { value = module.cluster.endpoint }
output "database_host" { value = module.database.host }
output "database_url" {
  value     = module.database.connection_url
  sensitive = true
}
