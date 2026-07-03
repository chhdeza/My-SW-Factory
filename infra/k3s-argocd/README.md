# Optional profile: k3s + Argo CD (GitOps)

This profile is NOT the default because it cannot run within AWS/GCP free tiers:
k3s + Argo CD realistically needs ~2 vCPU / 4 GB RAM (a paid VM, ~$10-25/month), and
managed Kubernetes control planes (EKS ~$73/month; GKE nodes) cost even more. Use it
when you already have a cluster or are happy to pay for a small VM.

## Install k3s on a VM

```bash
curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644
```

## Install Argo CD

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f \
  https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
# Get the initial admin password:
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d
```

## Point Argo CD at your app repo

Apply [application.yaml](application.yaml) after editing `repoURL` and `path`:

```bash
kubectl apply -n argocd -f application.yaml
```

Argo CD then keeps the cluster in sync with the manifests in your repo - merges that
pass the factory's human-gated deploy flow become the deployed state.

## Wiring to the factory

Set in `factory.yaml`:

```yaml
deploy:
  hook: "your_pkg.deploy_hooks:argocd_sync"   # custom dotted hook
```

A minimal hook can simply run `argocd app sync <app>` (or rely on auto-sync and just
verify health). The human approval gate still applies - the hook only runs after
approval.
