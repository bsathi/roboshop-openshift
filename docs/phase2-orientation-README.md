# Phase 2 — Get Oriented on the Live Sandbox Cluster
**Goal:** Learn your real resource ceiling and see a Project → Deployment → Service → Route flow work end-to-end, on a throwaway app, *before* touching roboshop.
**Time box:** 1–2 days.
**Prereqs:** Phase 1 complete — `oc login` working from WSL2.

---

## 1. Confirm your identity, project, and permissions

```bash
oc whoami
oc whoami --show-server
oc project
oc get project $(oc project -q) -o yaml | head -30
```

Look for the project's labels/annotations — Sandbox projects are usually named `<username>-dev`, `<username>-stage`, `<username>-code` (multiple projects for different purposes, but you typically can't create new ones beyond what's provisioned).

Check what you're actually allowed to do:

```bash
oc auth can-i --list
oc auth can-i create namespaces
oc auth can-i create scc
```

Expect most cluster-scoped verbs (creating projects, editing SCCs, installing Operators) to say `no` — that's expected for a self-service Sandbox account, not a misconfiguration.

---

## 2. Find your real resource quota

This number governs everything about how you'll trim the roboshop manifests later.

```bash
oc describe quota -n $(oc project -q)
oc describe limitrange -n $(oc project -q)
```

Write down (literally, in a notes file):
- Total CPU request/limit allowed
- Total memory request/limit allowed
- Max pods
- Any default per-container limits from the LimitRange (these get silently applied if your Deployment doesn't specify its own)

With ~10 components (4 datastores + 6 microservices) in roboshop, divide your quota by ~10-12 to get a rough per-container budget, and keep that number in mind for Phase 3.

---

## 3. Inspect the SCC you're bound to

```bash
oc get scc
oc get scc restricted-v2 -o yaml
oc describe serviceaccount default -n $(oc project -q)
```

Note the `runAsUser`, `runAsGroup`, and `fsGroup` strategy — it'll say something like `MustRunAsRange`, confirming pods get an arbitrary UID from an allowed range, not root and not a fixed UID.

---

## 4. Check available storage classes

Your `mysql`/`mongodb` PVCs will need a valid `storageClassName` — the Sandbox's won't be EKS's `gp2`.

```bash
oc get storageclass
```

Note the default one (marked `(default)` in the output) — you'll either omit `storageClassName` entirely to use the default, or set it explicitly.

---

## 5. Deploy a disposable "hello world" to see the full flow

This is the exercise that makes Routes, builds, and SCC behavior click before you touch a 10-component app.

```bash
oc new-app --name=hello-test docker.io/openshift/hello-openshift
oc get pods -w
```

Watch it go `Pending → ContainerCreating → Running`. Once running:

```bash
oc get svc hello-test
oc expose service/hello-test
oc get route hello-test
```

Take the `HOST/PORT` from `oc get route` and curl it (or open in a browser):

```bash
curl http://<the-route-host-from-above>
```

You should get a response back — this confirms Project → Deployment → Service → Route works without any LoadBalancer.

Check logs and describe to get familiar with the diagnostic commands you'll lean on heavily in Phase 3:

```bash
oc logs deployment/hello-test
oc describe pod -l app=hello-test
oc get events --sort-by=.lastTimestamp | tail -20
```

---

## 6. Deliberately break it to see an SCC failure (very instructive)

Try running something that wants root, to see exactly what a permission failure looks like — this is precisely the failure mode you'll hit with some of the `bsathi2020/*` images.

```bash
oc new-app --name=root-test docker.io/library/nginx
oc get pods -l app=root-test
oc logs deployment/root-test
```

Stock `nginx` images often fail to bind port 80 or write to `/var/cache/nginx` under the restricted SCC — you should see a permission-denied style error in the logs. Read it carefully; this is the exact error signature to watch for with roboshop's images.

Clean up both test apps once you've seen this:

```bash
oc delete all -l app=hello-test
oc delete all -l app=root-test
```

---

## 7. Explore the web console alongside the CLI

- Switch to the **Developer** perspective (toggle top-left) → **Topology** view — deploy `hello-test` again and watch it appear as a visual node with pod status, and Route linked as an icon on the node.
- Switch to **Administrator** perspective → browse **Workloads**, **Networking → Routes**, **Storage → PersistentVolumeClaims** so you know where to click when something's easier to inspect visually than via `oc describe`.
- Try the **+Add** button → "Container images" flow once, just to see the UI equivalent of `oc new-app`.

---

## 8. Checklist before starting Phase 3 (roboshop conversion)

- [ ] You know your exact CPU/memory/pod quota numbers (written down somewhere)
- [ ] You've seen a Route successfully expose a pod externally
- [ ] You've seen at least one real SCC/permission failure in `oc logs` output, and recognize the error text
- [ ] You know your default StorageClass name
- [ ] You're comfortable with `oc get/describe/logs/events` as your default debugging loop
- [ ] You've located the Topology view and Routes page in the web console

Once these are checked off, share your actual `01-namespace.yaml`, one Deployment+Service pair (e.g., `mysql` or `cart`), and the corresponding Dockerfile from `roboshop-docker` — Phase 3 will convert them concretely against the quota and SCC numbers you just gathered.
