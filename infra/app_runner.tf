resource "aws_apprunner_service" "api" {
  service_name = var.app_name

  source_configuration {
    image_repository {
      image_identifier      = "${aws_ecr_repository.api.repository_url}:latest"
      image_repository_type = "ECR"
      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          APP_ENV                      = "production"
          S3_BUCKET                    = aws_s3_bucket.jobs.bucket
          EXPIRE_JOB_LAMBDA_ARN        = aws_lambda_function.expire_job.arn
          SCHEDULER_EXECUTION_ROLE_ARN = aws_iam_role.scheduler_execution.arn
        }
        runtime_environment_secrets = {
          JWT_SECRET   = aws_ssm_parameter.jwt_secret.arn
          DATABASE_URL = aws_ssm_parameter.database_url.arn
        }
      }
    }
    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_ecr_access.arn
    }
    auto_deployments_enabled = false
  }

  instance_configuration {
    cpu               = "256"
    memory            = "512"
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }

  health_check_configuration {
    protocol = "HTTP"
    path     = "/health/"
    interval = 10
    timeout  = 5
  }

  depends_on = [aws_iam_role_policy_attachment.apprunner_ecr_access]
}

resource "aws_apprunner_custom_domain_association" "api" {
  domain_name          = "api.redactcat.com"
  service_arn          = aws_apprunner_service.api.arn
  enable_www_subdomain = false
}
