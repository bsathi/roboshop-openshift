#!/bin/bash
# incluster-run.sh — orchestrates one full in-cluster performance test run,
# invoked from the Jenkinsfile's "incluster" JMETER_RUNTIME branch (and runnable
# standalone for manual testing, exactly like job.yaml has been all along).
#
# Assumes: oc is installed and already logged into the target cluster (this
# script inherits whatever kubeconfig/token the invoking shell already has -
# the same "already-authenticated WSL2 environment" principle used for JMeter/
# Java throughout this whole project). If "oc whoami" fails, the Sandbox's
# short-lived SSO token has likely expired - re-run "oc login" manually first.
#
# On completion, retrieved artifacts are copied into the CURRENT directory as
# results.jtl, performance-report/, and order-results.txt - run this script
# from docs/performance-testing/ so those land exactly where quality_gate.py
# and the Jenkinsfile's existing Archive Results stage already expect them.

set -e

TARGET_HOST="${TARGET_HOST:-frontend-bsathi2020-dev.apps.rm3.7wse.p1.openshiftapps.com}"
PROTOCOL="${PROTOCOL:-http}"
PORT="${PORT:-80}"
THREADS="${THREADS:-10}"
RAMPUP="${RAMPUP:-10}"
LOOPS="${LOOPS:-5}"
POLL_TIMEOUT_SECONDS="${POLL_TIMEOUT_SECONDS:-600}"

echo "=== Verifying cluster access ==="
oc whoami || { echo "ERROR: not logged into the cluster - run 'oc login' first"; exit 1; }

echo "=== Ensuring results PVC exists ==="
oc get pvc roboshop-perftest-results >/dev/null 2>&1 || oc create -f pvc.yaml

echo "=== Ensuring no leftover retriever pod is still holding the PVC ==="
# A plain Pod (unlike a Job) has no automatic cleanup - if a previous manual
# retrieval session's retriever pod was never deleted, it keeps the PVC
# (ReadWriteOnce - one mounter at a time) permanently attached, causing a
# Multi-Attach error when this run's Job pod tries to mount the same volume.
if oc get pod roboshop-perftest-retriever >/dev/null 2>&1; then
    echo "Found a leftover retriever pod - deleting it before proceeding..."
    oc delete pod roboshop-perftest-retriever --ignore-not-found --wait=true --timeout=60s
    # Give the volume a moment to fully detach before the new Job pod tries to mount it -
    # deletion completing doesn't always mean the underlying EBS volume has finished
    # detaching yet.
    sleep 10
fi

echo "=== Rendering and creating the Job (TARGET_HOST=$TARGET_HOST THREADS=$THREADS RAMPUP=$RAMPUP LOOPS=$LOOPS) ==="
export TARGET_HOST PROTOCOL PORT THREADS RAMPUP LOOPS
envsubst < job-template.yaml > /tmp/job-run.yaml
oc create -f /tmp/job-run.yaml

JOB_NAME=$(oc get jobs -l app=roboshop-perftest --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}')
echo "Created Job: $JOB_NAME"

echo "=== Waiting for the Job to finish (timeout: ${POLL_TIMEOUT_SECONDS}s) ==="
WAITED=0
SUCCEEDED=""
FAILED=""
while [ "$WAITED" -lt "$POLL_TIMEOUT_SECONDS" ]; do
    SUCCEEDED=$(oc get job "$JOB_NAME" -o jsonpath='{.status.succeeded}' 2>/dev/null || echo "")
    FAILED=$(oc get job "$JOB_NAME" -o jsonpath='{.status.failed}' 2>/dev/null || echo "")
    if [ "$SUCCEEDED" = "1" ] || [ "$FAILED" = "1" ]; then
        break
    fi
    sleep 10
    WAITED=$((WAITED + 10))
done

echo "=== Job pod logs ==="
POD_NAME=$(oc get pods -l job-name="$JOB_NAME" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [ -n "$POD_NAME" ]; then
    oc logs "$POD_NAME" || true
else
    echo "WARNING: could not find a pod for Job $JOB_NAME (it may not have scheduled in time)"
fi

if [ "$SUCCEEDED" != "1" ] && [ "$FAILED" != "1" ]; then
    echo "ERROR: Job did not reach a terminal state within ${POLL_TIMEOUT_SECONDS}s - treating as a failure."
    FAILED="1"
fi

echo "=== Retrieving artifacts via a fresh retriever pod ==="
oc delete pod roboshop-perftest-retriever --ignore-not-found >/dev/null 2>&1
oc create -f retriever-pod.yaml
oc wait --for=condition=Ready pod/roboshop-perftest-retriever --timeout=60s

rm -rf performance-report results.jtl order-results.txt
oc cp roboshop-perftest-retriever:/results/results.jtl ./results.jtl
oc cp roboshop-perftest-retriever:/results/performance-report ./performance-report
oc cp roboshop-perftest-retriever:/results/order-results.txt ./order-results.txt 2>/dev/null || true

echo "=== Cleaning up ==="
oc delete pod roboshop-perftest-retriever --ignore-not-found
oc delete job "$JOB_NAME" --ignore-not-found

if [ "$SUCCEEDED" = "1" ]; then
    echo "=== In-cluster run SUCCEEDED ==="
    exit 0
else
    echo "=== In-cluster run FAILED ==="
    exit 1
fi
