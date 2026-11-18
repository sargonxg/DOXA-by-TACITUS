locals {
  name   = "doxa-${var.customer_id}"
  bucket = "${substr(var.project_id, 0, 24)}-doxa-${var.customer_id}"
  apis = toset([
    "compute.googleapis.com", "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com", "storage.googleapis.com", "iam.googleapis.com",
    "iap.googleapis.com", "oslogin.googleapis.com"
  ])
}
resource "google_project_service" "required" {
  for_each           = local.apis
  service            = each.value
  disable_on_destroy = false
}
resource "google_compute_network" "pilot" {
  name                    = local.name
  auto_create_subnetworks = false
  depends_on              = [google_project_service.required]
}
resource "google_compute_subnetwork" "pilot" {
  name                     = local.name
  region                   = var.region
  ip_cidr_range            = "10.83.0.0/24"
  network                  = google_compute_network.pilot.id
  private_ip_google_access = true
}
resource "google_compute_address" "pilot" {
  name   = local.name
  region = var.region
}
resource "google_compute_firewall" "https" {
  name          = "${local.name}-https"
  network       = google_compute_network.pilot.id
  target_tags   = [local.name]
  source_ranges = ["0.0.0.0/0"]
  allow {
    protocol = "tcp"
    ports    = ["80", "443"]
  }
}
resource "google_compute_firewall" "iap_ssh" {
  name          = "${local.name}-iap-ssh"
  network       = google_compute_network.pilot.id
  target_tags   = [local.name]
  source_ranges = ["35.235.240.0/20"]
  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
resource "google_service_account" "pilot" {
  account_id   = local.name
  display_name = "DOXA dedicated installation ${var.customer_id}"
  depends_on   = [google_project_service.required]
}
resource "google_secret_manager_secret_iam_member" "access" {
  secret_id = var.access_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.pilot.email}"
}
resource "google_secret_manager_secret_iam_member" "environment" {
  secret_id = var.environment_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.pilot.email}"
}
resource "google_secret_manager_secret_iam_member" "pricing" {
  secret_id = var.pricing_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.pilot.email}"
}
resource "google_artifact_registry_repository_iam_member" "image" {
  location   = var.region
  repository = var.artifact_repository
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.pilot.email}"
  depends_on = [google_project_service.required]
}
resource "google_compute_disk" "data" {
  name = "${local.name}-data"
  type = "pd-balanced"
  zone = var.zone
  size = var.data_disk_gb
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.required]
}
resource "google_compute_disk" "control" {
  name = "${local.name}-control"
  type = "pd-balanced"
  zone = var.zone
  size = 10
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.required]
}
resource "google_storage_bucket" "backup" {
  name                        = "${local.bucket}-backup"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  lifecycle_rule {
    condition { age = 35 }
    action { type = "Delete" }
  }
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.required]
}
resource "google_storage_bucket" "control" {
  name                        = "${local.bucket}-control"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = true }
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.required]
}
resource "google_storage_bucket_iam_member" "backup_create" {
  bucket = google_storage_bucket.backup.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.pilot.email}"
}
resource "google_storage_bucket_iam_member" "backup_read" {
  bucket = google_storage_bucket.backup.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.pilot.email}"
}
resource "google_storage_bucket_iam_member" "control_create" {
  bucket = google_storage_bucket.control.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.pilot.email}"
}
resource "google_storage_bucket_iam_member" "control_read" {
  bucket = google_storage_bucket.control.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.pilot.email}"
}
resource "google_compute_instance" "pilot" {
  name                      = local.name
  machine_type              = var.machine_type
  zone                      = var.zone
  allow_stopping_for_update = true
  deletion_protection       = true
  tags                      = [local.name]
  labels                    = { application = "doxa", installation = var.customer_id }
  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = 20
      type  = "pd-balanced"
    }
  }
  attached_disk {
    source      = google_compute_disk.data.id
    device_name = "doxa-data"
  }
  attached_disk {
    source      = google_compute_disk.control.id
    device_name = "doxa-control"
  }
  network_interface {
    subnetwork = google_compute_subnetwork.pilot.id
    access_config { nat_ip = google_compute_address.pilot.address }
  }
  service_account {
    email  = google_service_account.pilot.email
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }
  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }
  metadata = merge({
    enable-oslogin           = "TRUE"
    block-project-ssh-keys   = "TRUE"
    disable-legacy-endpoints = "TRUE"
    }, {
    startup-script = templatefile("${path.module}/startup.sh.tftpl", {
      project_id       = var.project_id
      access_secret    = var.access_secret_id
      env_secret       = var.environment_secret_id
      pricing_secret   = var.pricing_secret_id
      app_image        = var.app_image
      registry_host    = split("/", var.app_image)[0]
      caddy_image      = var.caddy_image
      domain           = var.domain
      kind             = var.workspace_kind
      control_bucket   = google_storage_bucket.control.name
      backup_bucket    = google_storage_bucket.backup.name
      initialize_empty = var.initialize_empty_workspace ? "true" : "false"
      cloud_store      = base64encode(file("${path.module}/cloud_store.py"))
      backup_script    = base64encode(file("${path.module}/backup.sh"))
      restore_script   = base64encode(file("${path.module}/restore.sh"))
    })
  })
  lifecycle {
    precondition {
      condition     = startswith(var.zone, "${var.region}-")
      error_message = "Zone must belong to the configured US region."
    }
    precondition {
      condition     = startswith(var.app_image, "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repository}/")
      error_message = "Image must belong to the configured repository and region."
    }
  }
  depends_on = [
    google_secret_manager_secret_iam_member.access,
    google_secret_manager_secret_iam_member.environment,
    google_secret_manager_secret_iam_member.pricing,
    google_artifact_registry_repository_iam_member.image,
    google_storage_bucket_iam_member.backup_create,
    google_storage_bucket_iam_member.backup_read,
    google_storage_bucket_iam_member.control_create,
    google_storage_bucket_iam_member.control_read
  ]
}
