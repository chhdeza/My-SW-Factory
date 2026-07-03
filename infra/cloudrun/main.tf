# GCP Cloud Run service for a factory-built app (fits the always-free tier at
# low traffic). Requires: gcloud auth or GOOGLE_APPLICATION_CREDENTIALS.

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

variable "region" {
  description = "Cloud Run region"
  type        = string
  default     = "us-central1"
}

variable "service" {
  description = "Cloud Run service name"
  type        = string
}

variable "image" {
  description = "Container image (e.g. gcr.io/project/app:tag)"
  type        = string
}

variable "app_ref" {
  description = "Git ref being deployed (recorded as a label)"
  type        = string
  default     = "manual"
}

provider "google" {
  project = var.project
  region  = var.region
}

resource "google_cloud_run_v2_service" "app" {
  name     = var.service
  location = var.region

  template {
    labels = {
      managed-by = "software-factory"
    }
    containers {
      image = var.image
      resources {
        limits = {
          # Free-tier friendly: smallest practical footprint.
          cpu    = "1"
          memory = "256Mi"
        }
      }
    }
    scaling {
      min_instance_count = 0 # scale to zero: no idle cost
      max_instance_count = 2 # bounded: cannot run up a bill
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.app.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" {
  value = google_cloud_run_v2_service.app.uri
}
