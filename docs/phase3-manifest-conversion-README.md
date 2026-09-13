# Phase 3 — k8-roboshop on Red Hat OpenShift Dev Sandbox
**Status: COMPLETE.** All 10 components running, full end-to-end flow validated (browse → register → login → add to cart → checkout → shipping → payment) across two independent test users (`bsathi`, `ssathi`).

This README is both the how-to *and* the debugging journal — every real error hit during this migration, its root cause, and the fix, in the order they actually occurred. Treat this as your primary upskilling artifact for this project: the RCA process here is the transferable skill, more so than any single fix.

---

## 1. What's actually in the repo (confirmed by extracting and reading every file)

```
01-namespace.yaml
cart/manifest.yaml         catalogue/manifest.yaml
debug/manifest.yaml        debug/Dockerfile
frontend/manifest.yaml     frontend/nginx.conf   (unused — see Section 5.3)
mongodb/manifest.yaml      mongodb/network-policy.yaml
mysql/manifest.yaml        payment/manifest.yaml
rabbitmq/manifest.yaml     redis/manifest.yaml
shipping/manifest.yaml     user/manifest.yaml
```

Confirmed up front, before any deployment: no PVCs anywhere (all datastores ephemeral by design), no `runAsUser`/`fsGroup` overrides in any manifest, ConfigMaps already use short Service names (no namespace-qualified FQDNs), and only `frontend` used `type: LoadBalancer`. This meant the "generic OpenShift migration checklist" (fix storage classes, strip security contexts, rewrite service discovery) mostly didn't apply here — the real issues turned out to be almost entirely about **container images assuming root**, not about the Kubernetes manifests themselves.

---

## 2. The one universal manifest fix: namespace

**Issue:** every file hardcoded `namespace: roboshop` (29 occurrences, 11 files). The Sandbox provisions one project per user with its own generated name (`bsathi2020-dev`) — you cannot create a `roboshop` namespace/project on the free tier.

**Fix:**
```bash
PROJECT=$(oc project -q)
find . -type f -name "*.yaml" -exec sed -i "s/namespace: roboshop/namespace: ${PROJECT}/g" {} \;
```
`01-namespace.yaml` was never applied — deployed into the pre-existing Sandbox project instead.

**Lesson:** manifests written for a self-managed cluster (EKS, where you own namespace creation) often hardcode the namespace name. On a shared/managed platform like a Sandbox, that assumption breaks immediately. This is a "day one" portability issue, not an OpenShift-specific one — the same fix would be needed moving to any cluster where you don't control the namespace name.

---

## 3. Service type fix: frontend only

**Issue:** `frontend/manifest.yaml` used `type: LoadBalancer`. The Sandbox has no permission to provision a cloud load balancer.

**Fix:** changed to `ClusterIP` (the default), external access instead provided by an OpenShift `Route`:
```bash
oc expose service/frontend
```

**Lesson:** `LoadBalancer` is a cloud-provider integration point — EKS satisfies it via an AWS NLB/ALB. OpenShift's answer to "give me a URL" is the `Route` object, which needs no cloud permissions and works identically on any OpenShift cluster, Sandbox or otherwise. This is one of the cleanest EKS→OpenShift conceptual differences: *never* try to replicate `LoadBalancer` behavior on OpenShift — use `Route` and think of it as a first-class replacement, not a workaround.

---

## 4. RCA journal — every real failure, in order

### 4.1 MongoDB — `CrashLoopBackOff`

**Symptom:** pod restarts continuously.

**Diagnosis:** `oc logs deployment/mongodb` showed mongod started normally (wire spec, replication services all initialized) then died on:
```
"Error creating journal directory","attr":{"directory":"/data/db/journal","error":"boost::filesystem::create_directory: Permission denied [system:13]"}
```

**Root cause:** OpenShift's default `restricted`/`restricted-v2` SCC assigns every pod an arbitrary, non-predictable UID — never root, never a fixed UID your Dockerfile might assume. The official `mongo:7.0` image's `/data/db` directory is owned by a fixed UID baked in at image-build time. The arbitrary Sandbox UID has no write access to it. This is a well-known, widely documented gap: the official Mongo image was never patched for OpenShift's arbitrary-UID model (unlike the official MySQL image — see 4.2).

**Fix — image-level, not manifest-level** (this class of problem has no manifest-side solution when there's no `runAsUser` to strip in the first place):
```dockerfile
FROM docker.io/library/mongo:7.0
RUN mkdir -p /data/db /data/configdb && \
    chgrp -R 0 /data/db /data/configdb && \
    chmod -R g=u /data/db /data/configdb
COPY *.js /docker-entrypoint-initdb.d
```
Rebuilt and pushed as a new tag (deliberately not overwriting `:v1`, to avoid silently breaking the working EKS deployment):
```bash
podman build -t docker.io/bsathi2020/mongodb:openshift .
podman push docker.io/bsathi2020/mongodb:openshift
```
Updated `mongodb/manifest.yaml`'s `image:` to `bsathi2020/mongodb:openshift`, `oc apply`, `oc delete pod -l component=mongodb` to force immediate recreation.

**Why `chgrp -R 0` + `chmod -R g=u` specifically:** the restricted SCC always places the arbitrary UID into supplementary group `0` (root group), even though the UID itself has no special privilege. Making a directory group-writable for group `0`, with the same permission bits as the owner (`g=u`), means *any* arbitrary UID that OpenShift assigns — which is always in group `0` — can read/write it, regardless of which specific UID number it happens to be. This is the standard, portable OpenShift pattern for "make this image run under an arbitrary UID" and applies to virtually any image with this class of problem.

**Side discovery — Podman config, not OpenShift:** the first build attempt failed before even reaching this fix:
```
Error: creating build container: short-name "mongo:7.0" did not resolve to an alias
```
Podman (unlike Docker) refuses to guess a registry for an unqualified image name. Fixed by fully-qualifying the base image: `FROM docker.io/library/mongo:7.0`. Unrelated to OpenShift, but worth remembering for any future image you build via Podman.

---

### 4.2 MySQL — worked immediately, no fix needed

**Why it's worth documenting a non-failure:** `mysql:8.0` (official image) started cleanly on the very first `oc apply`, no SCC issue at all, despite an equally custom Dockerfile and equally no `runAsUser` override in the manifest. Oracle's official MySQL image has built-in support for running under an arbitrary UID — a deliberate compatibility patch. Mongo's official image never received the equivalent treatment.

**Lesson:** "official image" does not imply "OpenShift-compatible image" — compatibility is decided per-project, not by Docker Hub's official status. Never assume; always test each image individually against the actual SCC, and expect that some official images will just work while others (RabbitMQ has this same class of issue, well-documented in the community, though we didn't end up hitting it directly in this deployment since it came up healthy) will not.

**Validated seed data** (via `/docker-entrypoint-initdb.d` auto-execution of `db/app-user.sql` + `db/master-data.sql`): database `cities`, tables `cities` (948,833 rows) and `codes` (25 rows) — confirmed via direct `mysql` client query.

---

### 4.3 Frontend (nginx) — `CrashLoopBackOff`, two rounds

**Round 1 symptom:**
```
nginx: [emerg] open() "/run/nginx.pid" failed (13: Permission denied)
```

**Root cause:** same arbitrary-UID pattern as mongodb — `/run` is root-owned in the base nginx image, and the pid file write fails under the restricted SCC. (The accompanying `the "user" directive makes sense only if the master process runs with super-user privileges` warning is unrelated noise — nginx just noting that `user nginx;` is meaningless when you aren't root; it doesn't cause the crash.)

**Fix chosen:** rather than a Dockerfile rebuild, redirect the pid path to `/tmp` (universally world-writable) directly in the nginx config:
```
pid /tmp/nginx.pid;
```

**Round 2 — same error persisted after the "fix," with a real gotcha:**

The fix was edited into the wrong file. `frontend/manifest.yaml` embeds its own copy of `nginx.conf` **inline**, as ConfigMap data, mounted into the container via `subPath`. The repo *also* contains a standalone `frontend/nginx.conf` file sitting in the same folder — but that file is never read by anything; it's a leftover/reference copy. The edit went into the standalone file first, so `oc apply` kept correctly reporting `configmap/frontend unchanged` — the object it was comparing against genuinely hadn't changed.

**Second gotcha, once the edit was in the right place:** even after fixing `manifest.yaml` and running `oc apply` (which this time correctly showed `configmap/frontend configured`), the already-running pod did *not* pick up the change automatically. This is a real, documented Kubernetes behavior: **`subPath` ConfigMap volume mounts do not live-sync** the way whole-directory ConfigMap mounts do — a `subPath` mount is effectively a one-time snapshot taken at pod creation. Fix: force a new pod so the mount is recreated fresh:
```bash
oc delete pod -l component=frontend
```

**Lessons:**
1. When a manifest embeds config inline via a ConfigMap, always double-check *which* file is actually the source of truth before editing — a repo can easily contain a stale duplicate that looks like the right file to edit.
2. `subPath` ConfigMap/Secret mounts are a known Kubernetes gotcha: they never auto-update. If you need live config reload without a pod restart, avoid `subPath` and mount the whole ConfigMap as a directory instead. If you knowingly use `subPath` (as this repo does, to place a single file at a specific path without other files in `/etc/nginx/` being affected), accept that every config change requires a pod recreation.
3. Verify fixes at the mounted-file level after the fact, not just from the ConfigMap object:
   ```bash
   oc exec deployment/frontend -- cat /etc/nginx/nginx.conf | grep pid
   ```

---

### 4.4 Redis — silent 500s in two unrelated-looking services (`user` and `cart`)

**Symptom:** frontend showed `Error[Object Object]` on "add to cart"; browser DevTools showed 500s on both `/api/user/uniqueid` and `/api/cart/add/...`. `oc logs deployment/user` was unhelpful — its own error handler logged an empty `"ERROR {}"`, no real detail.

**RCA process — this is the transferable part:**
1. Ruled out network/DNS first (cheap, fast test): `oc exec deployment/user -- node -e "require('net').createConnection(6379,'redis')..."` → connected successfully. This eliminated "Redis unreachable from user" as the cause and pointed toward an application- or Redis-level problem instead.
2. Since `user`'s own logs were unhelpful, got the *real* error from a different service hitting the same dependency: `oc logs deployment/cart` showed the actual exception clearly, because `cart`'s error logging happened to capture the Redis client library's error object properly where `user`'s did not:
   ```
   "command":"SETEX","code":"MISCONF","msg":"MISCONF Redis is configured to save RDB snapshots, but it's currently unable to persist to disk..."
   ```
3. This single log line from `cart` retroactively explained `user`'s failure too — both call Redis write commands (`cart` via `SETEX` for the cart contents, `user` almost certainly via a similar write for its `uniqueid` counter/session key), and both hit the exact same underlying Redis fault.

**Root cause:** yet another arbitrary-UID/write-permission problem, this time inside Redis itself — `redis:7.0`'s default config attempts periodic RDB snapshotting to disk (`BGSAVE`), and `stop-writes-on-bgsave-error` (default `yes`) makes Redis refuse *all* further write commands once a snapshot attempt fails. Since there's no PVC for redis anyway (ephemeral by design, consistent with every other datastore in this deployment), persistence was never actually needed here — the fix was to disable it rather than chase down a directory permission fix.

**Fix — manifest-level this time, no image rebuild needed:**
```yaml
containers:
- name: redis
  image: redis:7.0
  command: ["redis-server"]
  args: ["--save", "", "--stop-writes-on-bgsave-error", "no"]
```
`--save ""` clears all save points (disables RDB snapshotting entirely — Redis never attempts the failing write). `--stop-writes-on-bgsave-error no` is a safety-net second layer in case any save attempt is still triggered by something else.

**Verification approach:** rather than trust the browser alone, tested the exact backend calls directly, bypassing the UI:
```bash
oc exec deployment/frontend -- curl -s http://user:8080/uniqueid
oc exec deployment/frontend -- curl -s http://cart:8080/add/bsathi/EMM/2
```
Both returned clean JSON post-fix — confirmed the root cause before ever touching the browser again.

**Lessons:**
1. **When two unrelated services fail the same way, suspect a shared dependency, not two coincidental app bugs.** `user` and `cart` are different codebases (both Node.js, but independently maintained routes) — the shared failure pattern was the tell that pointed at Redis rather than either app.
2. **When one service's logs are unhelpful, check a sibling service hitting the same dependency** — `cart`'s better error logging solved `user`'s mystery for free.
3. **`redis-cli MONITOR`** (used earlier in this investigation, even though the `cart` log ultimately gave the answer first) is the definitive tool for "what command is actually being sent to Redis right now" — worth knowing for any future Redis-backed debugging, on OpenShift or anywhere else.
4. This was the *third* distinct instance of the same underlying category of problem (mongodb, frontend, redis) — by this point the pattern ("arbitrary UID can't write somewhere the image assumes it can") should be recognizable on sight from the words "Permission denied" or, as in this case, a database-specific error message about being unable to persist to disk.

---

### 4.5 Non-issues correctly identified as such (part of RCA discipline — knowing what *not* to chase)

| Observation | Why it's not a bug |
|---|---|
| `mongodb` had no `users` database, only `catalogue` | MongoDB creates databases **lazily**, on first write — `master-data.js` only seeds `catalogue`; `users` was confirmed to appear automatically the moment a real user registered through the UI |
| `/api/ratings/...` calls returned 404 | The `ratings` microservice was never part of this repo/deployment at all — frontend calls it optimistically and degrades gracefully |
| `/api/user/history/...` and `/api/cart/rename//...` returned 404 | Secondary/non-critical UI calls, unrelated to the core shopping flow, non-blocking |
| `@instana/collector ... Agent cannot be contacted` warnings in every service's logs | Built-in APM tracing trying to reach a local agent that was never deployed; harmless, self-retrying, ignorable noise |
| `oc exec -it deployment/redis -- redis-cli DBSIZE` hung with a blinking cursor | WSL2 pty-negotiation quirk specific to combining `-it` with a one-shot (non-interactive) command; command had already succeeded server-side. Fix: drop `-it` for any single-shot `oc exec` command; reserve it only for genuinely interactive sessions (`mysql -u root -p`, `mongosh`) |

---

## 5. Validation commands reference (all confirmed working during this deployment)

**MongoDB:**
```bash
oc exec deployment/mongodb -- mongosh --eval "show dbs"
oc exec deployment/mongodb -- mongosh catalogue --eval "db.products.countDocuments()"
```

**MySQL** (password `RoboShop@1`, from the Dockerfile's `MYSQL_ROOT_PASSWORD`):
```bash
oc exec -it deployment/mysql -- mysql -u root -p"RoboShop@1" -e "SELECT COUNT(*) FROM cities.cities;"
```

**Redis:**
```bash
oc exec deployment/redis -- redis-cli ping
oc exec deployment/redis -- redis-cli DBSIZE
oc exec deployment/redis -- redis-cli GET bsathi
```

**RabbitMQ:**
```bash
oc exec deployment/rabbitmq -- rabbitmqctl status
oc exec deployment/rabbitmq -- rabbitmqctl list_users
oc exec deployment/rabbitmq -- rabbitmqctl list_queues
```

**Direct service-to-service testing (bypassing the browser/Route entirely — the single most useful debugging technique used throughout this phase):**
```bash
oc exec deployment/frontend -- curl -s http://<service>:8080/<path>
```

---

## 6. Final checklist — all complete

- [x] `namespace: roboshop` replaced with real project name across all files
- [x] `frontend` Service changed from `LoadBalancer` to `ClusterIP`, exposed via Route
- [x] mysql `Running`, seed data validated (948,833 city rows)
- [x] mongodb `Running` — required custom image rebuild (`bsathi2020/mongodb:openshift`) with `chgrp -R 0`/`chmod -R g=u` fix; `catalogue` seed validated, `users` confirmed lazily-created on first registration
- [x] redis `Running` — required `--save ""` / `--stop-writes-on-bgsave-error no` args to avoid `MISCONF` write failures
- [x] rabbitmq `Running`, `roboshop` user confirmed
- [x] catalogue, user, cart, shipping, payment all `Running`
- [x] frontend `Running` — required pid-path fix (`/tmp/nginx.pid`) inside the manifest's embedded ConfigMap
- [x] Full end-to-end flow validated twice, with two separate user accounts (`bsathi`, `ssathi`): browse → register/login → add to cart → checkout with shipping address → place order with payment

## 7. Key takeaways for OpenShift work generally (portfolio/interview-ready summary)

1. **The restricted SCC (arbitrary non-root UID) is the single most common EKS→OpenShift migration blocker**, and it surfaced in three unrelated components here (mongodb, nginx, redis) — each with a different specific symptom but the identical underlying cause. Recognizing this pattern quickly is the highest-value OpenShift troubleshooting skill.
2. **Two fix strategies exist for this class of problem, and choosing correctly matters:** rebuild the image with `chgrp -R 0 && chmod -R g=u` on the affected directory (mongodb — needed because the write target was baked into the base image), versus reconfigure the running app to avoid the write entirely via command-line args or config (redis, frontend — needed because the fix could live entirely at the manifest/config layer, no rebuild required). Always check whether a manifest/config-level fix exists before reaching for a Dockerfile rebuild.
3. **`Route` replaces `LoadBalancer`/`Ingress`** as the OpenShift-native external access pattern — no cloud permissions required, works identically on any OpenShift cluster.
4. **RCA discipline**: reproduce directly (`oc exec ... curl`, bypassing the browser), check the *right* service's logs (not just the one the user-facing error implicates), recognize shared-dependency failures across otherwise-unrelated services, and explicitly rule out non-issues (lazy DB creation, unrelated 404s, APM noise) rather than chasing them.
5. **Environment quirks are not the same as platform bugs** — the Podman short-name resolution error and the WSL2 `oc exec -it` pty hang were both real obstacles, but neither was an OpenShift issue; both needed their own, unrelated fixes.

This deployment, with this documented RCA trail, is a genuinely strong artifact to reference in an OpenShift-focused interview — it demonstrates the actual day-to-day debugging skill (SCC troubleshooting, image vs. manifest fix selection, RCA methodology) that a Gov client running OpenShift would care about far more than a clean deploy with no incidents ever would.
