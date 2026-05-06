# =============================================================================
# NEXUS Production Cluster — DigitalOcean DOKS
# =============================================================================

# VPC for network isolation
resource "digitalocean_vpc" "nexus" {
  name        = "${var.cluster_name}-vpc"
  region      = var.do_region
  description = "VPC for NEXUS production cluster"
  ip_range    = "10.10.0.0/16"
}

# DOKS Cluster
resource "digitalocean_kubernetes_cluster" "nexus" {
  name         = var.cluster_name
  region       = var.do_region
  version      = var.k8s_version
  vpc_uuid     = digitalocean_vpc.nexus.id
  auto_upgrade = true
  ha           = false # HA control plane costs extra — not needed for PFE

  # Maintenance window: Sunday 4 AM UTC (low traffic)
  maintenance_policy {
    start_time = "04:00"
    day        = "sunday"
  }

  node_pool {
    name       = "${var.cluster_name}-workers"
    size       = var.node_size
    auto_scale = true
    min_nodes  = var.min_nodes
    max_nodes  = var.max_nodes
    node_count = var.node_count

    labels = {
      "nexus.io/role"    = "worker"
      "nexus.io/env"     = "production"
    }

    tags = var.tags
  }

  tags = var.tags
}
