output "address" { value = google_compute_address.pilot.address }
output "url" { value = "https://${var.domain}" }
output "instance" { value = google_compute_instance.pilot.name }
output "backup_bucket" { value = google_storage_bucket.backup.name }
output "deletion_ledger_bucket" { value = google_storage_bucket.control.name }
output "operator_ssh" {
  value = "gcloud compute ssh ${google_compute_instance.pilot.name} --project ${var.project_id} --zone ${var.zone} --tunnel-through-iap"
}
