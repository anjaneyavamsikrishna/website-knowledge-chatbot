# Deploy with GitHub Actions and Argo CD

## What is included

- Non-root Python 3.11 Docker image; one Uvicorn worker.
- CI tests PRs and builds the container. Main builds publish to GHCR, then commit manifests with the exact image digest to `deploy`.
- Argo CD follows `deploy:k8s` using the dedicated `website-knowledge-chatbot` project.
- Single replica with Recreate updates; expect brief downtime during updates. SQLite and ChromaDB share a 2 GiB local-path PVC. Chat sessions reset on restart.
- Secrets and optional ingress examples are outside the auto-synced manifests.

## 1. Add these files to main

Copy the package contents into the repository root, preserving `.github`, `.dockerignore`, and `.gitignore`. Review and commit through a branch/PR. Merge to main to start publishing, or manually run the workflow from main.

GitHub Settings > Actions > General > Workflow permissions must permit read/write. The workflow uses GITHUB_TOKEN; no Docker Hub password or cluster kubeconfig is needed. Repository rules must allow this workflow to create/update the `deploy` branch. Do not edit deploy by hand: CI replaces it from main's k8s directory.

Wait until the first workflow succeeds and creates the deploy branch before creating the Argo CD application.

## 2. Image and repository access

For a public image, explicitly set the GHCR package visibility to public after the first publish. A public GitHub repository does not guarantee a public GHCR package.

If keeping the image private, create a `ghcr-pull` docker-registry Secret in `website-knowledge-chatbot` using a token with read:packages, and add `imagePullSecrets: [{name: ghcr-pull}]` under Deployment `spec.template.spec` in main. Do not commit the token. For a private Git repository, also configure Argo CD repository credentials independently.

## 3. Prepare the cluster and application credentials

Use the intended cluster context. K3s must have the `local-path` StorageClass. Check available memory before deploying: a 1 GiB t3.micro is tight for K3s, Argo CD and this app together. Resource settings are a starting point, not a capacity guarantee; use API embeddings, not local embedding models.

```bash
kubectl get nodes
kubectl get storageclass local-path
kubectl apply -f k8s/namespace.yaml
cp docs/deployment/secret.yaml.example secret.yaml
chmod 600 secret.yaml
# Edit secret.yaml locally with your real provider key and random admin token.
kubectl apply -f secret.yaml
```

Never commit real credentials. `secret.yaml` is ignored. Choose the LLM and embedding models in `k8s/configmap.yaml` to match your provider. Configure base URLs there if using a compatible provider. Restart the deployment after changing Secret/ConfigMap values (env variables are read at startup).

## 4. Register with existing Argo CD

This assumes Argo CD is already installed in namespace argocd on the target cluster. These files do not install Argo CD itself.

```bash
kubectl apply -f argocd/project.yaml
kubectl apply -f argocd/application.yaml
kubectl -n argocd get application website-knowledge-chatbot
kubectl -n website-knowledge-chatbot get pods,pvc,svc
kubectl -n website-knowledge-chatbot rollout status deployment/website-knowledge-chatbot --timeout=300s
```

Automatic sync and self-heal are enabled; automatic pruning is disabled. The PVC also has Delete=false and Prune=false. Do not delete it to fix pod or image errors. Local-path storage stays on its node and is not a backup: back up the data before host changes.

## 5. Verify locally, then add the real domain

```bash
kubectl -n website-knowledge-chatbot port-forward svc/website-knowledge-chatbot 8000:80
# In another terminal:
curl http://localhost:8000/api/health
```

Open http://localhost:8000. A healthy process does not mean the knowledge base is populated. `knowledge_base_ready` is false until ingestion succeeds. With your admin token, call POST /api/ingest using the authentication header documented in the app README. Ingestion uses external provider APIs and may incur charges; it is not run automatically.

For HTTPS, replace `chatbot.example.com` in `docs/deployment/ingress.yaml.example` with your real domain, confirm Traefik and the `letsencrypt-prod` ClusterIssuer exist, copy the file to `k8s/ingress.yaml`, and add `ingress.yaml` to resources in `k8s/kustomization.yaml`. Point DNS to the ingress address and merge to main. Do not apply the placeholder example unchanged.

Uvicorn uses its default trusted proxy list. Configure FORWARDED_ALLOW_IPS with the actual trusted proxy IPs/networks if client IP based rate limiting is required; do not trust arbitrary forwarded headers from directly reachable clients.

## Troubleshooting and rollback

- Pending PVC: local-path WaitForFirstConsumer waits for a schedulable pod; inspect pod events and resource availability.
- ImagePullBackOff: check the GHCR package visibility or imagePullSecret.
- CreateContainerConfigError: ensure chatbot-secrets exists in the same namespace.
- Missing deploy revision: fix the first CI run before syncing Argo CD.
- Failed deploy-branch push: check workflow write permissions and branch rules.
- To roll back, revert the relevant application commit on main; CI rebuilds and publishes a new digest. Do not rely on a manual cluster image change while self-heal is enabled.

## Validation performed

119 existing tests passed locally (Python 3.12); CI runs Python 3.11 to match the image. YAML parsing, manifest references, Service selectors, PVC wiring and workflow shell syntax passed. Docker and kubectl are unavailable in the preparation workspace, so an actual image build, Kustomize render and live rollout remain to be verified by CI and the target cluster.
