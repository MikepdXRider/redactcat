output "ecr_repository_url" {
  value       = aws_ecr_repository.api.repository_url
  description = "ECR repository URL — used as the docker push target"
}

output "app_runner_service_url" {
  value       = "https://${aws_apprunner_service.api.service_url}"
  description = "Public App Runner service URL (before custom domain)"
}

output "s3_jobs_bucket" {
  value       = aws_s3_bucket.jobs.bucket
  description = "S3 bucket name for ephemeral job file storage"
}

output "github_actions_role_arn" {
  value       = aws_iam_role.github_actions.arn
  description = "IAM role ARN assumed by GitHub Actions via OIDC for deploy"
}

output "route53_nameservers" {
  value       = aws_route53_zone.main.name_servers
  description = "Update redactcat.com registrar to use these nameservers"
}
