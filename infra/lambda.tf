
# Build the Lambda zip: install psycopg2-binary for Amazon Linux into vendor/, copy handler.
# Reruns whenever expire_jobs.py changes.
resource "null_resource" "lambda_package" {
  triggers = {
    source_hash = filemd5("${path.module}/lambda/expire_jobs.py")
  }

  provisioner "local-exec" {
    command = <<-EOT
      pip3 install psycopg2-binary \
        --platform manylinux2014_x86_64 \
        --implementation cp \
        --python-version 3.12 \
        --only-binary=:all: \
        -q \
        -t ${path.module}/lambda/vendor/ --upgrade
      cp ${path.module}/lambda/expire_jobs.py ${path.module}/lambda/vendor/
    EOT
  }
}

data "archive_file" "expire_jobs" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/vendor"
  output_path = "${path.module}/lambda/expire_jobs.zip"
  depends_on  = [null_resource.lambda_package]
}

resource "aws_lambda_function" "expire_job" {
  function_name    = "${var.app_name}-expire-job"
  role             = aws_iam_role.lambda_expire_job.arn
  runtime          = "python3.12"
  handler          = "expire_jobs.handler"
  filename         = data.archive_file.expire_jobs.output_path
  source_code_hash = data.archive_file.expire_jobs.output_base64sha256
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      S3_BUCKET             = aws_s3_bucket.jobs.bucket
      DATABASE_URL_SSM_PATH = "/${var.app_name}/DATABASE_URL"
    }
  }
}

resource "aws_iam_role" "lambda_expire_job" {
  name = "${var.app_name}-lambda-expire-job"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_expire_job" {
  name = "${var.app_name}-lambda-expire-job"
  role = aws_iam_role.lambda_expire_job.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = aws_ssm_parameter.database_url.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.jobs.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:DeleteObject"]
        Resource = "${aws_s3_bucket.jobs.arn}/*"
      }
    ]
  })
}
