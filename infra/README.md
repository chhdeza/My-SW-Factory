# Infrastructure (target apps)

OpenTofu modules for deploying the apps the factory builds. The factory itself runs
locally or on GitHub Actions runners - it is never deployed here.

All deploys are executed by `factory deploy execute` (or the `deploy.yml` workflow) and
only after a human approves them (dashboard button or protected GitHub Environment).

| Module | Target | Free-tier fit |
|---|---|---|
| [cloudrun/](cloudrun/) | GCP Cloud Run service from a container image | Yes - generous always-free tier (requests, CPU, memory) |
| [vm-compose/](vm-compose/) | Single VM running docker compose | Yes - GCP `e2-micro` (always free) or AWS `t2.micro`/`t3.micro` (12-month free tier) |
| [k3s-argocd/](k3s-argocd/) | Optional: k3s + Argo CD GitOps profile | No - needs a paid VM (~2 vCPU / 4 GB); documented for when you have one |

## Usage

```bash
cd infra/cloudrun          # or vm-compose
tofu init
tofu plan -var image=gcr.io/my-project/my-app:sha
```

Wire the module to the factory by setting `deploy.hook` in `factory.yaml` to `cloudrun`
or `compose_vm` and filling in the matching variables block. Credentials come from the
environment (`GOOGLE_APPLICATION_CREDENTIALS`, `AWS_*`) - never from committed files.
