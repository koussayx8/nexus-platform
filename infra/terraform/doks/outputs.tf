output "cluster_id" {
  description = "DOKS cluster ID"
  value       = digitalocean_kubernetes_cluster.nexus.id
}

output "cluster_endpoint" {
  description = "Kubernetes API server endpoint"
  value       = digitalocean_kubernetes_cluster.nexus.endpoint
  sensitive   = true
}

output "cluster_name" {
  description = "Cluster name"
  value       = digitalocean_kubernetes_cluster.nexus.name
}

output "cluster_version" {
  description = "Kubernetes version running on the cluster"
  value       = digitalocean_kubernetes_cluster.nexus.version
}

output "kubeconfig" {
  description = "Kubeconfig for cluster access (sensitive)"
  value       = digitalocean_kubernetes_cluster.nexus.kube_config[0].raw_config
  sensitive   = true
}

output "vpc_id" {
  description = "VPC ID for the cluster network"
  value       = digitalocean_vpc.nexus.id
}

output "node_pool_id" {
  description = "Worker node pool ID"
  value       = digitalocean_kubernetes_cluster.nexus.node_pool[0].id
}
