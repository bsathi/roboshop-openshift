# Phase 0 — OpenShift Core Concepts
**Goal:** Understand what OpenShift adds on top of Kubernetes before touching the cluster, so Phase 2/3 errors make sense instead of feeling random.
**Time box:** 2–3 evenings, no cluster access required.
**Prereqs:** Your existing K8s/EKS knowledge (Deployments, Services, PVCs, ConfigMaps, Secrets).

---

## 1. Mental model: OpenShift = K8s + opinionated defaults + extra objects

OpenShift Container Platform (and the free-tier "Developer Sandbox for Red Hat OpenShift") is CNCF-certified Kubernetes underneath. `kubectl` manifests mostly work as-is. What changes:

| Concern | Vanilla K8s / EKS | OpenShift |
|---|---|---|
| Namespace | `Namespace`, self-service, anyone can create | `Project` — a Namespace + extra RBAC/quota wrapper. On the free Sandbox you get **one pre-provisioned project**, you don't create your own. |
| External access | `Ingress` or `Service type: LoadBalancer` (needs a cloud LB) | `Route` — OpenShift's own object, no external LB required, works out of the box on shared/sandbox clusters |
| Container UID | Whatever the Dockerfile's `USER` says, often root | **Restricted SCC** assigns a random high-numbered UID by default. Root-requiring images break unless fixed. |
| Building images | You build with `docker build` externally and push | Can optionally build **inside the cluster** via `BuildConfig` + **S2I (Source-to-Image)**, producing an `ImageStream` |
| Declarative rollout object | `Deployment` (also works fine on OpenShift) | `DeploymentConfig` (OpenShift-native, older, has extra triggers) — new projects mostly use plain `Deployment` now, but you'll see `DeploymentConfig` in older examples |
| Installing packaged software | Helm charts, raw manifests | Same, plus **Operators** via OperatorHub (databases, message queues, monitoring, etc. as managed CRDs) |
| CLI | `kubectl` | `oc` — superset of `kubectl`, adds `oc new-app`, `oc expose`, `oc rollout`, `oc get routes`, etc. |

**Key takeaway for your roboshop migration:** the manifests will *mostly* apply as-is. The failures you'll hit are almost entirely about (a) the Restricted SCC and non-root UIDs, (b) `LoadBalancer` Services needing to become Routes, and (c) quota being much tighter than an EKS cluster you fully control.

---

## 2. Objects to know cold before Phase 2

### Project
- `oc get projects`, `oc project <name>` to switch.
- On the free Sandbox: you get one project like `<username>-dev`. You generally **cannot** create additional projects — plan to deploy the whole roboshop app into that single project.

### Route
- Exposes a Service externally via an auto-generated or custom hostname, with TLS termination handled for you (edge/passthrough/reencrypt).
- `oc expose service/frontend` is the fast path; equivalent to writing a `Route` YAML by hand.
- This replaces whatever `Ingress`/`LoadBalancer` object your `k8-roboshop` manifests use for the frontend.

### Security Context Constraints (SCC)
- The single most important OpenShift-specific concept for your migration.
- Default SCC for normal users = `restricted` (or `restricted-v2` on newer versions): no root, arbitrary UID, must belong to group `0` for any UID-agnostic file writes.
- Consequence: any image whose entrypoint assumes it can `chown`/write as root, or that hardcodes `USER 1000`, may crash with permission errors even though it works fine on EKS.
- Fix pattern (to apply to Dockerfiles later): `RUN chgrp -R 0 /app-data && chmod -R g=u /app-data` — this makes the arbitrary UID (which is always in group `0`) able to read/write.
- `oc get scc`, `oc describe scc restricted` — read these once so you recognize the fields (`runAsUser`, `fsGroup`, `allowedCapabilities`).

### BuildConfig / ImageStream / S2I (stretch goal, Phase 4 later)
- `BuildConfig`: describes how to build an image inside the cluster (from source, a Dockerfile, or S2I).
- `S2I` (Source-to-Image): a builder image (e.g., Node.js, Java) combined with your app source to produce a runnable image without you writing a Dockerfile.
- `ImageStream`: an OpenShift abstraction over image tags, gives you automatic rollout triggers when a new image lands.
- Not required for your first pass (you already have Docker Hub images) — but worth knowing this exists as the "OpenShift-native" alternative.

### Operators / OperatorHub
- Kubernetes Operators packaged for one-click install from the web console (databases, Kafka, monitoring stacks, GitOps, Pipelines).
- On the Sandbox, install permissions may be limited — you'll mostly consume Operators the cluster admin pre-installed (e.g., **OpenShift GitOps**, **OpenShift Pipelines**) rather than installing new ones yourself.

### `oc` CLI vs `kubectl`
- `oc` is a superset — anything `kubectl` does, `oc` does too (`oc apply -f`, `oc get pods` all work identically).
- OpenShift-only conveniences: `oc new-app`, `oc new-build`, `oc expose`, `oc rollout status/latest`, `oc get route`, `oc logs -f dc/<name>` (for DeploymentConfigs), `oc whoami`, `oc project`.

---

## 3. Free Developer Sandbox specifics to internalize now

- **Time-boxed**: the sandbox typically expires after a set number of days of inactivity/total lifetime and needs periodic reactivation from the Red Hat developer portal.
- **Single shared project**, not a cluster you administer — no cluster-admin rights, no creating new namespaces/projects, no installing arbitrary Operators.
- **Resource quota is tight** — expect single-digit vCPU and a handful of GB of RAM total across *everything* you deploy. Running mysql + mongodb + redis + rabbitmq + 6 microservices at their EKS-sized resource requests will likely exceed it. Plan on single replicas and trimmed `resources.requests/limits` from the start.
- **No external LoadBalancer provisioning** — Routes are the only way out.

---

## 4. Self-check before moving to Phase 1

You should be able to answer these without looking anything up:
1. What replaces `Ingress`/`LoadBalancer` on OpenShift, and why?
2. Why might a Docker Hub image that runs fine on EKS crash on OpenShift with a "permission denied" error?
3. What's the difference between a `Project` and a `Namespace`?
4. What does `oc new-app` do that `kubectl apply -f deployment.yaml` doesn't?
5. Why can't you just copy your `01-namespace.yaml` into the Sandbox and apply it?

If any of these are fuzzy, skim the OpenShift docs "Architecture" and "Authentication and authorization / Managing SCCs" pages before Phase 1.

---

## 5. Reference links
- OpenShift docs: https://docs.openshift.com/
- Developer Sandbox: https://developers.redhat.com/developer-sandbox
- SCC deep dive: search "Managing security context constraints" in OpenShift docs
- `oc` CLI reference: `oc --help` and `oc <subcommand> --help` (best single source once installed)
