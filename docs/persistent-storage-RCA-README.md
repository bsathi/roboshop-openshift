# Adding Persistent Storage to roboshop-openshift — RCA Journal

**Branch:** `persistent-storage`
**Scope:** adding `PersistentVolumeClaim`-backed storage to `mysql` and `mongodb`, so their data survives pod restarts and the Sandbox's automatic workload idling (both of which previously reset all data via the `/docker-entrypoint-initdb.d` re-seed mechanism).

This document covers four distinct, non-obvious failures hit while adding persistence — each looked at first like a repeat of an already-solved problem (the OpenShift restricted-SCC arbitrary-UID pattern from the base migration, or an earlier issue in this same document), and each turned out to be something different, including one case where **the fix for an earlier issue was itself the bug**. That mismatch between "looks familiar" and "is actually familiar" is the main lesson of this document, as much as any individual fix.

---

## Background: why persistence wasn't trivial to add

The base `roboshop-openshift` deployment (see `main` branch, and `docs/phase3-manifest-conversion-README.md`) runs `mysql` and `mongodb` with no `PersistentVolumeClaim` at all — each container's own filesystem holds its data, which is why every pod restart triggers a full re-seed via `/docker-entrypoint-initdb.d`. That's invisible with static reference data (the `cities` table, the `catalogue` products) but would silently lose real data (orders, user accounts) in a genuinely persistent app. Adding a PVC sounds like a small, mechanical change — mount a volume instead of using the container's own disk — but it exposed four separate assumptions the original setup never had to confront.

---

## Issue 1 — `lost+found` triggers a false "data directory not empty" error

**Symptom:** immediately after adding a PVC to `mysql/manifest.yaml` and applying it, the new pod crash-looped with:
```
[ERROR] [MY-010457] [Server] --initialize specified but the data directory has files in it. Aborting.
```

**Initial (wrong) hypothesis:** given the project's history, the first assumption was that this was the same restricted-SCC/arbitrary-UID permission problem already solved for `mongodb` earlier in the project — a plausible guess, but wrong. `oc describe pod` showed `SuccessfulAttachVolume` had already succeeded and the container itself started and ran before erroring, ruling out a permission failure.

**Actual root cause:** a freshly-provisioned AWS EBS volume (via the `gp3` StorageClass) is not truly empty once mounted — the CSI driver formats it with `mkfs`, which automatically creates a `lost+found` directory at the volume's root. MySQL's `--initialize` bootstrap logic treats *any* file or directory in the target path as evidence the directory was previously used, and refuses to proceed rather than risk overwriting real data. `lost+found` alone was enough to trip this check, despite containing no actual data.

**Fix (first attempt):** an `initContainer` that removes `lost+found` before the main `mysql` container's entrypoint runs. This version turned out to be incomplete — see Issue 2.

**Lesson:** a PVC is not "the same directory, just persistent" — its starting contents depend on the CSI driver/filesystem format, and can differ from both a container's own baked-in filesystem and from what you'd naively expect "empty" to mean. Always verify actual PVC contents directly (`ls -la` via a disposable inspector pod) rather than assuming.

---

## Issue 2 — Undersized PVC causes a silent, self-perpetuating initialization failure

**Symptom:** even after the `lost+found` fix above, the pod continued to crash-loop with the *identical* error, indefinitely — including immediately after independently confirming (via a disposable `busybox` pod mounting the same PVC) that the volume was genuinely, completely empty.

**Diagnosis process:** this is the part worth internalizing more than the specific fix. The failure looked identical to Issue 1, but empty-volume-plus-same-error meant Issue 1's explanation could no longer be correct — something else was writing files and then dying, every single time, regardless of starting state. Comparing file timestamps in the PVC against the pod's actual start time confirmed real InnoDB files (`ibdata1`, `undo_001`, `undo_002`, the doublewrite buffer) were being created *during* the current attempt, not left over from before.

**Root cause:** `mysql/manifest.yaml`'s ConfigMap sets:
```yaml
[mysqld]
innodb_redo_log_capacity = 2G
```
The PVC was provisioned at exactly `2Gi`. The redo log configuration alone claimed the entire volume's capacity, leaving no room for `ibdata1`, the undo logs, the doublewrite buffer, or any table data — MySQL ran out of disk space partway through its own first-time initialization, died without a clear "disk full" message in this MySQL version's log output, and left a partial, corrupt-looking data directory behind. That partial directory then triggered the *same* "has files in it" check as Issue 1 on every subsequent restart — a different root cause producing an indistinguishable symptom.

**Fix:** resize the PVC well beyond the redo log's own footprint:
```yaml
# mysql/pvc.yaml
spec:
  resources:
    requests:
      storage: 8Gi   # was 2Gi
```
Given the Sandbox quota had 80Gi available and only 2 PVCs in use, there was no real constraint forcing the original undersized value — it was simply an unexamined default that happened to collide with an unrelated config setting.

**Lesson:** when a fix doesn't resolve a crash-loop, don't assume the fix was wrong — confirm whether the *starting conditions* the fix was supposed to guarantee actually held (in this case: was the directory genuinely empty this time?) before concluding the same error means the same cause. Two unrelated bugs sharing one error message is a realistic scenario, not a coincidence to dismiss.

---

## Issue 3 — `RollingUpdate` + `ReadWriteOnce` PVC causes a self-inflicted deadlock

**Symptom, for `mongodb` specifically:** after fixing the manifest and PVC, a new pod crash-looped with a *different* error than mysql's:
```
DBException in initAndListen, terminating: DBPathInUse: Unable to lock the lock file: /data/db/mongod.lock
(Resource temporarily unavailable). Another mongod instance is already running on the /data/db directory
```
`oc get pods` showed **two** mongodb pods running simultaneously — one old, one new.

**Root cause:** a `Deployment`'s default `RollingUpdate` strategy deliberately creates the *new* pod before terminating the *old* one, to avoid downtime during a normal (stateless) rollout. That's the correct behavior for a stateless app — but both pods here were configured to mount the **same** `ReadWriteOnce` PVC. MongoDB explicitly refuses to run two instances against one data directory, correctly protecting against data corruption, by holding an exclusive lock file. The result: the new pod could never start successfully while the old one held the volume, and the old pod's own ReplicaSet kept recreating it every time a pod was manually deleted — a genuine deadlock, not a timing race that would eventually resolve on its own.

**Compounding factor during diagnosis:** deleting individual pods didn't fix this, because the *ReplicaSet* behind the old pod — not just the pod itself — still had a desired replica count of 1 and kept respawning it. The actual fix required recognizing that two ReplicaSets existed simultaneously (`oc get rs`), each trying to satisfy the same Deployment's old and new pod-template versions, both racing for one exclusive-lock volume.

**Fix:** change the Deployment's update strategy from the default `RollingUpdate` to `Recreate`, which fully terminates the old pod before creating any new one — eliminating the possibility of two pods ever mounting the PVC at once:
```yaml
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    ...
```
Applied to both `mysql/manifest.yaml` and `mongodb/manifest.yaml`.

**Lesson:** `RollingUpdate` and `ReadWriteOnce` persistent volumes are fundamentally in tension for single-replica stateful workloads — this isn't specific to OpenShift or to this app, it's a general Kubernetes pattern worth recognizing on sight. Any Deployment (as opposed to StatefulSet) backed by an RWO PVC should default to `Recreate` unless there's a specific reason not to.

---

## Issue 4 — The fix for Issue 1 silently destroyed real data on every restart

This is the most important issue in this document, because it wasn't a deployment failure at all — it was a **passing** dry-run test that was quietly doing the wrong thing.

**How it was discovered:** during a full clean dry-run validation (a fresh clone, fresh project, fresh PVCs, deployed exactly per the README), the persistence test — delete the mysql pod, confirm the seed data survives — instead came back with the pod in `CrashLoopBackOff`, showing the exact same "has files in it" error from Issue 1, on a pod that should have simply restarted against already-valid data.

**Root cause — read the original Issue 1 fix again, literally:**
```yaml
command: ["sh", "-c", "rm -rf /var/lib/mysql/lost+found; rm -rf /var/lib/mysql/*"]
```
This init container was written to solve a **first-boot** problem (a fresh PVC's `lost+found` blocking initialization) but was never made conditional on it actually being a first boot. It ran, unchanged, on **every single pod start** — including ordinary restarts of a pod with fully valid, already-initialized data sitting on the PVC. Every restart silently wiped `/var/lib/mysql` clean via `rm -rf /var/lib/mysql/*`, then let MySQL re-initialize from scratch. This is a genuine, serious regression: it defeats the entire purpose of adding persistent storage, while *appearing* to work perfectly on every first deployment (the case that had been tested most) since a fresh deploy and a "wipe and reinitialize" produce identical, healthy-looking output.

**Why this is worse than Issues 1-3:** those three all failed loudly, immediately, as a crash-loop — impossible to miss. Issue 4 failed silently and would have shipped as "confirmed working" if the validation process hadn't specifically included a delete-and-recheck step. A fix that only gets tested against the scenario it was written for (first boot) can hide a regression in the scenario it wasn't tested against (a routine restart) indefinitely.

**Fix attempt 1 (incomplete):** make the wipe conditional on whether initialization had already happened, checking for one specific marker file:
```yaml
command: ["sh", "-c", "if [ ! -f /var/lib/mysql/ibdata1 ]; then rm -rf /var/lib/mysql/lost+found; fi"]
```
This correctly stopped wiping real data — but introduced a *narrower* version of the same class of bug. During testing, a pod that had previously died mid-initialization (before ever creating `ibdata1`) left behind a different partial artifact (`ib_buffer_pool`, from an even earlier failed attempt). The `ibdata1`-only check saw no `ibdata1`, concluded "this must be a fresh volume," and left the stray `ib_buffer_pool` file untouched — which was, on its own, still enough to trigger MySQL's "has files in it" abort. Checking for one specific file is fragile precisely because a partially-failed initialization can stop at any point and leave any subset of files behind.

**Fix attempt 2 (correct):** don't check for a specific file at all — check whether the directory has *any* content beyond `lost+found`:
```yaml
command: ["sh", "-c", "COUNT=$(ls -A /var/lib/mysql | grep -v lost+found | wc -l); if [ \"$COUNT\" -eq 0 ]; then rm -rf /var/lib/mysql/lost+found; fi"]
```
This says: if `lost+found` is the *only* thing present, it's safe to clear it and let MySQL initialize fresh. If there's anything else at all — a fully-initialized database, or partial debris from any point in a failed initialization — do nothing, and let MySQL's own entrypoint logic (or a human, if it's genuinely broken) decide what to do with it. Verified correct by: (1) a first deploy against a genuinely empty PVC initializing successfully, and (2) — the test that actually matters — a subsequent pod restart against that same, now-populated PVC coming back up with the original row count intact and **no** "Initializing database files" log line, confirming no re-initialization occurred.

**Lesson, the most important one in this document:** a fix is not validated by confirming the original symptom is gone — it's validated by testing the fix against every scenario the *change itself* now touches, including ones that weren't broken before. Issue 1's fix was tested only against "does first deploy work now?" and passed. It needed to also be tested against "does a normal restart still preserve data?" — the second question wasn't a stretch or an edge case, it was the entire stated purpose of the branch. When writing any init container, sidecar, or startup script that conditionally destroys or resets state, explicitly enumerate and test every state it might encounter (fresh, fully-initialized, and every plausible partially-failed state in between) rather than testing only the one state that prompted the fix.

---

## Summary table

| Issue | Looked like | Actually was | Fix |
|---|---|---|---|
| 1 | Same SCC/arbitrary-UID pattern as the original mongodb migration | Fresh EBS volume's auto-created `lost+found` tripping MySQL's "is this empty?" check | `initContainer` removing `lost+found` before the main container starts |
| 2 | Issue 1 recurring / fix not working | PVC too small for the app's own `innodb_redo_log_capacity` setting, causing a silent out-of-space failure mid-initialization | Resize PVC from 2Gi to 8Gi |
| 3 | A random timing flake during rollout | `RollingUpdate`'s old+new pod overlap deadlocking against a `ReadWriteOnce` volume's exclusive lock | `strategy: { type: Recreate }` on the Deployment |
| 4 | A dry-run validation that should have passed | The Issue 1 fix unconditionally wiped `/var/lib/mysql` on *every* pod start, silently destroying real data on ordinary restarts; a first, narrower fix (checking only for `ibdata1`) still missed other forms of partial-init debris | Check whether the directory contains *anything* besides `lost+found` before deciding it's safe to clean, rather than checking for one specific expected file |

## Diagnostic techniques used repeatedly across all four (worth reusing on any future PVC issue)

- **Disposable inspector pods** to look inside a PVC directly, independent of the workload that's crashing:
  ```bash
  oc run pvc-inspect --image=busybox --restart=Never --rm -i --tty --overrides='{"spec":{"containers":[{"name":"pvc-inspect","image":"busybox","command":["sh","-c","ls -la /data"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}],"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"<pvc-name>"}}]}}'
  ```
- **Comparing file/pod timestamps** to distinguish "leftover from before" versus "written during this attempt."
- **Checking `oc get rs` in addition to `oc get pods`** whenever a Deployment's rollout behaves unexpectedly — the ReplicaSet, not the pod, is the actual unit of "desired state" that can keep recreating something you thought you deleted.
- **Verifying init containers actually ran and succeeded** (`initContainerStatuses`, exit code) rather than assuming empty log output means success.
- **Never trusting `oc logs deployment/<name>` when more than one pod might match** — it silently picks one, which can be the *wrong* one mid-rollout. Always confirm the specific pod name first with `oc get pods -l component=<name>`.
- **Testing a fix against restart, not just first-deploy, for anything touching persistent state** — the single most important technique this document adds, given Issue 4. A full validation cycle for any stateful-storage change is: deploy fresh → confirm data present → delete the pod → confirm the *same* data is still present on the replacement → confirm no re-initialization log line appeared. Skipping the last two steps is how Issue 4 nearly shipped as "working."
