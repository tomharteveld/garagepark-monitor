#!/usr/bin/env bash
# One-time Google Cloud setup for the GaragePark monitor (keyless auth from GitHub Actions).
# Run in Cloud Shell or locally after `gcloud auth login`.
#
#   PROJECT_ID=follo-seo GITHUB_REPO=follo-agency/garagepark-monitor bash scripts/setup_gcp.sh
#
# Creates: BigQuery API access, a service account, a Workload Identity pool + GitHub provider
# that only trusts the given repository, and the IAM bindings. Prints the two values to put
# in GitHub as repository variables.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
: "${GITHUB_REPO:?set GITHUB_REPO as owner/repo}"
SA_NAME="${SA_NAME:-garagepark-monitor}"
POOL="${POOL:-github}"
PROVIDER="${PROVIDER:-github-actions}"
DATASET="${BQ_DATASET:-garagepark_monitor}"
LOCATION="${BQ_LOCATION:-EU}"

gcloud config set project "$PROJECT_ID" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
OWNER="${GITHUB_REPO%%/*}"

echo "Enabling APIs"
gcloud services enable bigquery.googleapis.com iamcredentials.googleapis.com sts.googleapis.com iam.googleapis.com

echo "Service account $SA"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NAME" --display-name "GaragePark monitor (GitHub Actions)"

echo "BigQuery dataset $DATASET ($LOCATION)"
bq --location="$LOCATION" show "$PROJECT_ID:$DATASET" >/dev/null 2>&1 || \
  bq --location="$LOCATION" mk --dataset --description "GaragePark lokale Search & AI monitor" "$PROJECT_ID:$DATASET"

echo "IAM: run jobs in the project, edit only this dataset"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$SA" --role roles/bigquery.jobUser --condition=None >/dev/null
bq add-iam-policy-binding --member="serviceAccount:$SA" --role="roles/bigquery.dataEditor" "$PROJECT_ID:$DATASET" >/dev/null

echo "Workload Identity pool + provider (trusts only $GITHUB_REPO)"
gcloud iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location=global --display-name "GitHub Actions"
gcloud iam workload-identity-pools providers describe "$PROVIDER" --location=global --workload-identity-pool="$POOL" >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location=global --workload-identity-pool="$POOL" \
    --display-name "GitHub repo" --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --attribute-condition "assertion.repository == '${GITHUB_REPO}' && assertion.repository_owner == '${OWNER}'"

gcloud iam service-accounts add-iam-policy-binding "$SA" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" >/dev/null

echo
echo "Done. Add these GitHub repository variables (Settings > Secrets and variables > Actions > Variables):"
echo "  GCP_PROJECT_ID       = $PROJECT_ID"
echo "  GCP_WIF_PROVIDER     = projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "  GCP_SERVICE_ACCOUNT  = $SA"
echo "  BQ_DATASET           = $DATASET"
echo "  BQ_LOCATION          = $LOCATION"
