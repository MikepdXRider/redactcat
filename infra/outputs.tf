output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "app_runner_service_url" {
  value = "https://${aws_apprunner_service.api.service_url}"
}

output "s3_jobs_bucket" {
  value = aws_s3_bucket.jobs.bucket
}

output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}

output "route53_nameservers" {
  value       = aws_route53_zone.main.name_servers
  description = "Update redactcat.com registrar to use these nameservers"
}
