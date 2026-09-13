# Phase 1 — Client Tooling Setup (Ubuntu 26.04 WSL2 + VS Code)
**Goal:** Get `oc`, `podman`, and VS Code's OpenShift/Kubernetes tooling working from your existing WSL2 Ubuntu 26.04 shell, and log in to your Developer Sandbox.
**Time box:** Half a day.
**Prereqs:** Ubuntu 26.04 on WSL2, `code -r .` already working from the Ubuntu shell, a Red Hat Developer account with an active Developer Sandbox.

---

## 1. Install the `oc` CLI (includes `kubectl`)

Use the Red Hat mirror rather than a generic `kubectl` binary — it matches the server version and gives you `oc`-only subcommands.

```bash
mkdir -p ~/bin
cd /tmp
curl -LO https://mirror.openshift.com/pub/openshift-v4/x86_64/clients/ocp/stable/openshift-client-linux.tar.gz
tar -xzf openshift-client-linux.tar.gz -C ~/bin oc kubectl
rm openshift-client-linux.tar.gz
```

Add `~/bin` to your PATH if it isn't already (append to `~/.bashrc` or `~/.zshrc`):

```bash
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

Verify:

```bash
oc version --client
kubectl version --client
```

> Tip: the exact download URL/version can also be copied directly from your Sandbox web console — click the **?** (help) icon → **Command Line Tools** → "Download oc" — this gives you a build matched to your cluster's server version, which is the safer choice if the generic `stable` link above is ever out of sync.

---

## 2. Install Podman

OpenShift's build/runtime ecosystem is rootless-container-first, and Podman's CLI is close enough to Docker that your existing `roboshop-docker` workflow transfers directly.

```bash
sudo apt update
sudo apt install -y podman
podman --version
```

If you want a drop-in `docker` alias for muscle memory:

```bash
sudo apt install -y podman-docker
```

(This isn't required for anything in Phase 1/2 — only relevant later if you rebuild any of the `bsathi2020/*` images.)

---

## 3. Install Helm (optional but useful — your `k8-roboshop` conventions may expand to Helm later)

```bash
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
helm version
```

---

## 4. VS Code extensions

From your WSL2 Ubuntu shell (with the VS Code Remote-WSL connection already active via `code -r .`), install these — either via the Extensions pane (they'll install into the WSL side automatically) or from the command line:

```bash
code --install-extension redhat.vscode-openshift-connector
code --install-extension ms-kubernetes-tools.vscode-kubernetes-tools
code --install-extension redhat.vscode-yaml
code --install-extension ms-azuretools.vscode-containers
```

- **Red Hat OpenShift Toolkit** (`redhat.vscode-openshift-connector`) — adds an OpenShift explorer pane, login UI, and right-click "Deploy" actions on manifests.
- **Kubernetes** (`ms-kubernetes-tools.vscode-kubernetes-tools`) — cluster explorer, works against both `kubectl` and `oc` contexts.
- **YAML** (`redhat.vscode-yaml`) — schema validation; it auto-detects Kubernetes/OpenShift schemas as you type, which will catch a lot of manifest typos before you `apply`.
- **Container Tools** — Dockerfile/Podman integration, useful once you're editing the `roboshop-docker` Dockerfiles.

Reload the window after installing (`Ctrl+Shift+P` → "Developer: Reload Window").

---

## 5. Get your Sandbox login credentials

1. Log in at https://developers.redhat.com/developer-sandbox and open your active sandbox.
2. In the OpenShift web console (the same URL you shared, e.g. `console-openshift-console.apps.<cluster>.<domain>`), click your **username (top right) → Copy login command**.
3. Click **Display Token** — this shows a command like:
   ```
   oc login --token=sha256~XXXXXXXXXXXXXXXXXXXX --server=https://api.<cluster>.<domain>:6443
   ```

Tokens expire (typically ~24h for Sandbox), so you'll repeat this step each session — it's normal, not a setup mistake.

---

## 6. Log in from WSL2 and verify

```bash
oc login --token=sha256~XXXXXXXXXXXXXXXXXXXX --server=https://api.<cluster>.<domain>:6443
oc whoami
oc project
```

You should see your username and your single pre-provisioned project (something like `<username>-dev`).

Also connect the VS Code OpenShift extension: open the OpenShift panel in the sidebar → **Login to Cluster** → it will pick up your existing `oc` session automatically if you're already logged in via terminal (they share `~/.kube/config`).

---

## 7. Sanity checklist before Phase 2

- [ ] `oc version --client` and `kubectl version --client` both print a version
- [ ] `podman --version` prints a version
- [ ] `helm version` prints a version (if installed)
- [ ] VS Code shows the OpenShift extension icon in the sidebar, WSL-side
- [ ] `oc whoami` returns your Red Hat Developer username
- [ ] `oc project` shows your Sandbox project name
- [ ] You can open the web console in a browser and see the same project

If `oc login` fails with a certificate error, it usually means the token expired mid-copy/paste — go back to the console and re-copy. If VS Code doesn't see your login, check that `~/.kube/config` exists (`cat ~/.kube/config | head`) and that the WSL2 VS Code window is the Remote-WSL one, not a native Windows VS Code window pointed at a different config path.
