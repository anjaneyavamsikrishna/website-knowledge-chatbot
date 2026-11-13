# Deploy from main with GitHub Actions, GHCR and Argo CD

## Deployment files

| File | Purpose |
| --- | --- |
| `Dockerfile`, `.dockerignore` | Non-root Python 3.11 image, one Uvicorn worker, no credentials or local data in the build context. |
| `.github/workflows/ci-cd.yaml` | Test and build on PRs; pushes to main publish to GHCR and commit the immutable image digest back to main. |
| `k8s/kustomization.yaml` | Active resource list and the last successfully published image digest. |
| `k8s/namespace.yaml` | Dedicated website-knowledge-chatbot namespace. |
| `k8s/deployment.yaml` | Single replica, Recreate updates, health probes and persistent /app/data mount. |
| `k8s/service.yaml` | ClusterIP port 80 to container named port http (8000). |
| `k8s/pvc.yaml` | Existing chatbot-data, 2 GiB, local-path; protected from Argo pruning/deletion. |
| `k8s/configmap.yaml` | Non-secret runtime defaults. |
| `argocd/project.yaml` | Allowed repository, destination and resource types. |
| `argocd/application.yaml` | Argo CD watches main:k8s with automatic sync and self-heal; pruning disabled. |
| `docs/deployment/secret.yaml.example` | Credential template only. Never sync real credentials from this public repository. |
| `docs/deployment/ingress.yaml.example` | Traefik HTTPS template; requires a real domain and a verified ClusterIssuer before activation. |

There is no deployment-branch dependency. The old deploy branch is no longer used once the live Application follows main. Application files such as app/, config/, scripts/ and tests/ remain unchanged. render.yaml belongs to the separate Render hosting option; Kustomize and Argo CD do not load it.

## CI/CD behavior

Push application/workflow/Kubernetes changes to main. CI runs tests and renders the manifests, then builds and publishes ghcr.io/anjaneyavamsikrishna/website-knowledge-chatbot with the source commit tag. Only after publishing succeeds does CI update k8s/kustomization.yaml on main with the image digest. Argo CD then deploys that digest.

The workflow uses GITHUB_TOKEN with contents:write and packages:write. No personal GHCR token or kubeconfig is needed in GitHub Actions. Branch rules must allow its non-force digest commit to main. If protected-main rules block it, the workflow fails; do not disable branch protections blindly.

The digest commit uses [skip ci], and pushes changing only k8s/kustomization.yaml are ignored. GITHUB_TOKEN pushes also do not recursively start push workflows. Concurrent source changes cause an older build to skip deployment; publishing retries non-force pushes up to three times. Manual runs publish only when selected on main. PRs test and build but do not publish or update manifests. Docs, argocd and .gitignore-only pushes do not rebuild the image. Argo CD still observes Git changes independently of Actions.

## Cluster setup

Use the intended cluster. K3s needs local-path storage and sufficient memory for the app, Argo CD and system workloads. These manifests do not install Argo CD, Traefik or cert-manager.

```bash
sudo k3s kubectl get nodes
sudo k3s kubectl get storageclass local-path
sudo k3s kubectl apply -f k8s/namespace.yaml
```

Create a private file ~/chatbot.env on the server. Put real ADMIN_TOKEN and LLM_API_KEY values there; EMBEDDING_API_KEY may be empty to reuse LLM_API_KEY. Use DATA_DIR=/app/data. Do not put credentials in GitHub, chat, ConfigMaps or command-line arguments. Replace any previously exposed credentials.

```bash
chmod 600 ~/chatbot.env
sudo k3s kubectl -n website-knowledge-chatbot create secret generic chatbot-secrets \
  --from-env-file="$HOME/chatbot.env" --dry-run=client -o yaml | sudo k3s kubectl apply -f -
sudo k3s kubectl apply -f argocd/project.yaml
sudo k3s kubectl apply -f argocd/application.yaml
```

The Deployment reads ConfigMap defaults first, then chatbot-secrets. If an entire env file is imported into the Secret, overlapping Secret values override the ConfigMap. Keep provider, model and base URL values consistent. Current non-secret defaults match the user's Gemini configuration; model availability is not asserted by these manifests. An invalid provider API key cannot be repaired by changing Kubernetes YAML.

After credential/config changes, restart and verify:

```bash
sudo k3s kubectl -n website-knowledge-chatbot rollout restart deployment/website-knowledge-chatbot
sudo k3s kubectl -n website-knowledge-chatbot rollout status deployment/website-knowledge-chatbot --timeout=180s
sudo k3s kubectl -n website-knowledge-chatbot get pods,pvc,svc,ingress
```

The existing image was pulled successfully without an imagePullSecret. If GHCR visibility later becomes private, create a read:packages registry Secret in this namespace and reference it using imagePullSecrets. Git repository authentication is separate from GHCR image authentication.

## Access and Ingress

Until a real domain is provided, Ingress remains disabled. Do not deploy chatbot.example.com or a hostless catch-all route.

```bash
sudo k3s kubectl -n website-knowledge-chatbot port-forward svc/website-knowledge-chatbot 8000:80
```

This listens on the server's localhost. To use a Windows browser, forward port 8000 over SSH to that server or run kubectl port-forward from a client with cluster access. Open http://127.0.0.1:8000 after the tunnel is established.

To enable HTTPS:

1. Confirm the desired hostname and point its DNS to the ingress address.
2. Verify Traefik and the intended cert-manager ClusterIssuer on this cluster.
3. Copy docs/deployment/ingress.yaml.example to k8s/ingress.yaml.
4. Replace both chatbot.example.com hosts and set the verified issuer.
5. Add `- ingress.yaml` to resources in k8s/kustomization.yaml and commit both files to main.
6. Verify the Ingress, certificate and HTTPS endpoint after sync.

## Knowledge base and end-to-end verification

A Ready pod and Argo Healthy show process health, not successful provider authentication or populated knowledge. AUTO_INGEST_ON_STARTUP is false to avoid automatic API calls during deployments.

```bash
sudo k3s kubectl -n website-knowledge-chatbot exec deployment/website-knowledge-chatbot -- \
  python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/api/health").read().decode())'
sudo k3s kubectl -n website-knowledge-chatbot exec deployment/website-knowledge-chatbot -- python scripts/ingest.py
```

Ingestion fetches the configured sources and uses provider quota. Expect knowledge_base_ready=true, chunks greater than zero and no failed sources; then test real chat responses. The last observed ingestion failed with HTTP 400 "Please pass a valid API key". Rotate/correct the key, update the Secret and restart before retrying. A non-empty key is not proof of authentication.

## Storage and rollback

Keep the existing namespace and chatbot-data PVC. Recreate deployment strategy avoids simultaneous writers to SQLite/ChromaDB but causes a brief outage during rollouts. local-path data stays on this node; back it up independently. Sessions are in memory and reset on restart.

To roll back code, revert the relevant source commit on main; CI publishes its rebuilt image and updates the digest. To pin an older image immediately, commit its verified digest to k8s/kustomization.yaml on main; that digest-only change does not rebuild. A later source build will advance the digest again. Do not use manual cluster image changes while self-heal is enabled.
