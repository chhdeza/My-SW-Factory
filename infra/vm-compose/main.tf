# Single free-tier VM running the app via docker compose.
# Default: GCP e2-micro (always free in us-west1/us-central1/us-east1).
# For AWS, see variables below - t2.micro/t3.micro is free for 12 months.

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

variable "project" {
  description = "GCP project id"
  type        = string
}

variable "zone" {
  description = "Zone (always-free zones: us-west1-*, us-central1-*, us-east1-*)"
  type        = string
  default     = "us-central1-a"
}

variable "name" {
  description = "Instance name"
  type        = string
  default     = "factory-app"
}

variable "compose_file" {
  description = "Path to the docker-compose.yml to deploy"
  type        = string
  default     = "docker-compose.yml"
}

variable "app_ref" {
  description = "Git ref being deployed (recorded as a label)"
  type        = string
  default     = "manual"
}

provider "google" {
  project = var.project
}

resource "google_compute_instance" "app" {
  name         = var.name
  machine_type = "e2-micro" # always-free tier
  zone         = var.zone

  labels = {
    managed-by = "software-factory"
  }

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 30 # GB - free tier allows up to 30GB standard PD
      type  = "pd-standard"
    }
  }

  network_interface {
    network = "default"
    access_config {} # ephemeral public IP
  }

  metadata_startup_script = <<-EOT
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v docker >/dev/null; then
      curl -fsSL https://get.docker.com | sh
    fi
    mkdir -p /opt/app
    # The deploy hook copies ${var.compose_file} to /opt/app via scp before apply,
    # or bake it into a custom image. Compose picks it up here:
    cd /opt/app && docker compose up -d || true
  EOT

  scheduling {
    preemptible       = false
    automatic_restart = true
  }
}

resource "google_compute_firewall" "http" {
  name    = "${var.name}-http"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }

  source_ranges = ["0.0.0.0/0"]
  target_tags   = []
}

output "public_ip" {
  value = google_compute_instance.app.network_interface[0].access_config[0].nat_ip
}
