variable "aws_region" {
  default = "us-east-1"
}

provider "aws" {
  region = "${var.aws_region}"
}

variable "DD_API_KEY" {
  description = "This token is used to associate AWS CloudWatch logs to a log in your Logentries account."
}

variable "function_name" {
  default = "dd_log_appender"
}

variable "role" {
  description = "arn of the lambda role"
}

variable "memory_size" {
  default = 1024
}

variable "timeout" {
  default = 300
}

variable "metadata" {
  type    = "map"
  default = {}
}

variable "log_tags" {
  type        = "map"
  default     = {}
  description = "map of custom key:value dd tags on each log statement"
}

variable "tags" {
  type    = "map"
  default = {}
}

data "archive_file" "fn" {
  type        = "zip"
  source_file = "${path.module}/lambda_function.py"
  output_path = "${path.module}/lambda_function.py.zip"
}

resource "aws_lambda_function" "fn" {
  function_name    = "${var.function_name}"
  role             = "${var.role}"
  handler          = "lambda_function.lambda_handler"
  filename         = "${path.module}/lambda_function.py.zip"
  source_code_hash = "${data.archive_file.fn.output_base64sha256}"

  # Set memory to 128 MB
  memory_size = "${var.memory_size}"

  # Set timeout to ~2 minutes (script only runs for seconds at a time)
  timeout = "${var.timeout}"
  runtime = "python2.7"

  environment {
    variables = {
      DD_API_KEY  = "${var.DD_API_KEY}"
      METADATA    = "${jsonencode(var.metadata)}"
      CUSTOM_TAGS = "${jsonencode(var.log_tags)}"
    }
  }

  tags = "${var.tags}"

  depends_on = ["data.archive_file.fn"]
}

output "function_arn" {
  value = "${aws_lambda_function.fn.arn}"
}

output "function_name" {
  value = "${aws_lambda_function.fn.function_name}"
}

output "function_version" {
  value = "${aws_lambda_function.fn.version}"
}
