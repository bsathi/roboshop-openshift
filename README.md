# roboshop-openshift

The [roboshop](https://github.com/bsathi/k8-roboshop) microservices e-commerce app, adapted to run on **Red Hat OpenShift Service on AWS** (validated on the free Developer Sandbox), built as a hands-on Kubernetes → OpenShift upskilling project.

Originally deployed via Docker + Kubernetes on AWS EKS ([`k8-roboshop`](https://github.com/bsathi/k8-roboshop)). This repo contains the manifest changes needed to run the same application on OpenShift, plus a full written record of every real issue hit along the way and how it was diagnosed and fixed.

> Companion repo: [roboshop-docker-openshift](https://github.com/bsathi/roboshop-docker-openshift) — the one container image rebuild this migration required.

---

## What's different from the original EKS manifests

| Change | Why |
|---|---|
| `namespace: roboshop` → your actual OpenShift project name | OpenShift Sandbox/managed projects are pre-provisioned with a generated name; you can't create a namespace called `roboshop` yourself |
| `frontend` Service: `LoadBalancer` → `ClusterIP` + `Route` | OpenShift has no cloud LoadBalancer provisioning on a Sandbox; `Route` is the native replacement, no cloud permissions required |
| `redis` Deployment: added `--save "" --stop-writes-on-bgsave-error no` | Redis's default RDB snapshot write failed under OpenShift's restricted SCC (no PVC/writable path); disabled since this app runs Redis ephemeral by design anyway |
| `frontend` embedded nginx config: `pid /run/nginx.pid;` → `pid /tmp/nginx.pid;` | `/run` isn't writable under OpenShift's arbitrary, non-root pod UID; `/tmp` is |
| `mongodb` image: `bsathi2020/mongodb:v1` → `bsathi2020/mongodb:openshift` | The official Mongo image isn't patched for OpenShift's arbitrary-UID model; a rebuilt image ([source in the companion repo](https://github.com/bsathi/roboshop-docker-openshift)) fixes `/data/db` permissions |

Everything else — ConfigMaps, Secrets, Service discovery via short Service names, application code — is unchanged from the original repo.

---

## Deployment order

```
mysql, mongodb, redis, rabbitmq   (datastores)
   ↓
catalogue, user, cart, shipping, payment   (microservices)
   ↓
frontend   (exposed via Route)
```

```bash
oc apply -f mysql/manifest.yaml -f mongodb/manifest.yaml -f redis/manifest.yaml -f rabbitmq/manifest.yaml
oc apply -f catalogue/manifest.yaml -f user/manifest.yaml -f cart/manifest.yaml -f shipping/manifest.yaml -f payment/manifest.yaml
oc apply -f frontend/manifest.yaml
oc expose service/frontend
oc get route frontend
```

Watch each component with `oc get pods -w` / `oc logs deployment/<name>` before moving to the next — see `docs/phase3-manifest-conversion-README.md` for the full deployment walkthrough and troubleshooting patterns.

---

## Learning path (`docs/`)

This repo doubles as a self-contained OpenShift upskilling curriculum. Work through it in order:

| File | Covers |
|---|---|
| `docs/phase0-concepts-README.md` | OpenShift vs. Kubernetes concepts — Projects, Routes, SCCs, ImageStreams/BuildConfig, Operators, the `oc` CLI |
| `docs/phase1-setup-README.md` | Client tooling setup on Ubuntu/WSL2 + VS Code, Sandbox login |
| `docs/phase2-orientation-README.md` | Hands-on cluster orientation — quota, SCC behavior, a disposable test deploy |
| `docs/phase3-manifest-conversion-README.md` | **The full migration + root-cause-analysis journal** — every real error hit deploying this app (SCC/permission failures, `subPath` ConfigMap gotchas, and more), the diagnosis process, and the exact fix, in the order it actually happened |
| `docs/OpenShift-Upskilling-Session-Transcript.docx` | Full prompt-by-prompt record of the debugging session this repo came out of |

If you're new to OpenShift, **Phase 3's RCA journal is the most valuable read in this repo** — it's written to teach the debugging pattern (recognizing OpenShift's restricted SCC / arbitrary-UID behavior on sight), not just document the fix.

---

## Prerequisites

- A Red Hat OpenShift cluster or [free Developer Sandbox](https://developers.redhat.com/developer-sandbox)
- `oc` CLI ([install docs](https://docs.openshift.com/container-platform/latest/cli_reference/openshift_cli/getting-started-cli.html))
- The seeded datastores expect the images from [`roboshop-docker-openshift`](https://github.com/bsathi/roboshop-docker-openshift) (mongodb) and the original `bsathi2020/*` Docker Hub images (everything else)

## License

MIT — see `LICENSE`. Free to fork, adapt, and use for your own learning or to teach others.
