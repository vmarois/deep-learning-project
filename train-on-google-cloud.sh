#!/usr/bin/env bash
set -euo pipefail

# PROJECT_ID: your project's id. Use the PROJECT_ID that matches your Google Cloud Platform project.
# BUCKET_ID: the bucket in which the HyperParameter Tuning models will be stored.
export PROJECT_ID=georgia-tech-ajouandin3
export BUCKET_ID=transformer_trained_model

export DATE_ID=$(date +%Y%m%d_%H%M%S)
export JOB_DIR=gs://${BUCKET_ID}/hp_tuning/${DATE_ID}
export IMAGE_REPO_NAME=transformer_hp_tuning_pytorch_container
export IMAGE_TAG=transformer_hp_tuning_pytorch
export IMAGE_URI=gcr.io/${PROJECT_ID}/${IMAGE_REPO_NAME}:${IMAGE_TAG}
export REGION=us-east1
export JOB_NAME=hp_tuning_container_job_${DATE_ID}

docker build -f Dockerfile -t ${IMAGE_URI} ./
docker run ${IMAGE_URI} --epochs 1 --is-test
docker push ${IMAGE_URI}

gcloud beta ai-platform jobs submit training $JOB_NAME \
  --job-dir=${JOB_DIR} \
  --region=${REGION} \
  --master-image-uri ${IMAGE_URI} \
  --config=hptuning_config.yaml \
  --scale-tier BASIC-GPU \
  -- \
  --batch-size 96
