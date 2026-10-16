# The solver as an AWS Lambda container image.
#
# Both Lambda functions (the web API and the job worker) run from this one image; the template picks
# the function with the command. Built from the repository root:
#   docker build -f infra/Dockerfile -t othello .
FROM public.ecr.aws/lambda/python:3.13

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLCONFIGDIR=/tmp/matplotlib

# Install the project and the picture library (boto3 is already part of the Lambda base image).
COPY pyproject.toml /tmp/build/pyproject.toml
COPY src /tmp/build/src
RUN pip install "/tmp/build[viz]" && rm -rf /tmp/build

# The web API by default; the template overrides this for the worker.
CMD ["othello.service.lambda_handler.handler"]
