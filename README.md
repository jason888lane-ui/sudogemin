# Sudogemin

FastAPI application for Sudogemin. Project IDX (now part of Firebase Studio) is the development environment; the API itself runs as a Python web service. AWS API Gateway and SigV4 are not required.

## Run in IDX / Firebase Studio

Open the repository in your workspace, then run in the terminal:

```bash
cd backend/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Open the workspace preview on port `8080`. The API root is `/`, health check is `/health`, and interactive API docs are `/docs`.

## Deploy to Google Cloud Run

From the repository root, with Google Cloud CLI installed and authenticated:

```bash
gcloud run deploy sudogemin-api \
  --source backend/api \
  --region us-central1 \
  --allow-unauthenticated
```

Cloud Run builds from `backend/api/Dockerfile` and supplies the `PORT` environment variable. Configure secrets such as Stripe credentials in Cloud Run's environment/secrets settings; do not commit secrets.

## Optional Cloudflare Worker proxy

`src/index.js` is an optional, unsigned HTTP proxy for the FastAPI service; it is not needed to run or deploy the API from IDX. If you use it, set the Worker variable `BACKEND_URL` to the HTTPS URL of your deployed API. It no longer needs AWS credentials or SigV4 signing.
