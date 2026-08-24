# Running the deploy job on MicroK8s with ARC

Replaces the self-hosted runner that used to live on a laptop. Runner pods are
ephemeral and scale to zero, so nothing holds memory between deploys.

Target cluster: `mbarukville-mgt-01`, single node, **amd64**, Ubuntu 24.04,
k8s v1.30.14. The same cluster already runs the InfluxDB this project writes to
(`influxdb.mbaruk.com` → `192.168.13.50`, `solarmonitoring` namespace).

## 1. Cluster prerequisites

```bash
microk8s enable helm3
```

`helm` below means `microk8s helm3` unless you have a standalone helm.

## 2. Create the GitHub App

Org settings → Developer settings → GitHub Apps → New GitHub App.

- Repository permissions: **Administration** read/write (this is what registers
  runners), **Metadata** read, **Actions** read.
- No webhook needed.
- Generate a private key and download the `.pem`.
- Install the App on **only** `MbarukInc/solar-inverter-monitor`.

Note the **App ID** and, from the installation URL, the **Installation ID**.

## 3. Create the secrets

Neither of these is ever committed. Both read from files so no value lands in
your shell history.

```bash
kubectl create namespace arc-runners
```

```bash
kubectl -n arc-runners create secret generic arc-github-app \
  --from-literal=github_app_id=<APP_ID> \
  --from-literal=github_app_installation_id=<INSTALLATION_ID> \
  --from-file=github_app_private_key=./your-app.private-key.pem
```

Then the deploy key. Generate a dedicated keypair — do not reuse a personal one:

```bash
ssh-keygen -t ed25519 -f ./pi_deploy -C "arc-deploy" -N ""
ssh-copy-id -i ./pi_deploy.pub pi@<PI_IP>
```

Capture the Pi's host key **from a machine you trust on the LAN**. This is the
trust anchor for every future deploy, so do not skip it and do not substitute
`StrictHostKeyChecking=no`:

```bash
ssh-keyscan -H <PI_IP> > ./known_hosts
```

```bash
kubectl -n arc-runners create secret generic pi-deploy-ssh \
  --from-file=id_ed25519=./pi_deploy \
  --from-file=known_hosts=./known_hosts
```

Delete `./pi_deploy` afterwards; the cluster has it now.

## 4. Build the runner image

The official image has no `rsync` and no `openssh-client`, both of which the
deploy job needs. Either path works — the import path avoids configuring an
insecure registry in Docker Desktop.

**Without a registry** (single node, simplest):

```bash
docker build --platform linux/amd64 -f deploy/arc/runner.Dockerfile -t actions-runner-deploy:2.336.0 . && docker save actions-runner-deploy:2.336.0 | ssh <NODE> 'microk8s ctr image import -'
```

Then set `image: docker.io/library/actions-runner-deploy:2.336.0` in
`scale-set-values.yaml`.

**With the built-in registry:**

```bash
microk8s enable registry
```

```bash
docker build --platform linux/amd64 -f deploy/arc/runner.Dockerfile -t 192.168.13.40:32000/actions-runner-deploy:2.336.0 . && docker push 192.168.13.40:32000/actions-runner-deploy:2.336.0
```

Pushing from a Mac needs `192.168.13.40:32000` added to Docker Desktop's
insecure registries (Settings → Docker Engine).

Note `--platform linux/amd64`: the cluster node is amd64 and your Mac is arm64,
so a default build produces an image the node cannot run.

## 5. Install ARC

```bash
helm upgrade --install arc oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller --namespace arc-systems --create-namespace
```

```bash
helm upgrade --install mbarukville oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set --namespace arc-runners -f deploy/arc/scale-set-values.yaml
```

The Helm release name is the runner label. Keep it `mbarukville` or the
workflows stop matching.

## 6. Verify

```bash
kubectl -n arc-systems logs -l app.kubernetes.io/name=gha-rs-controller --tail=50
```

The scale set should appear under repo Settings → Actions → Runners, and
`kubectl -n arc-runners get pods` shows nothing until a job arrives.

## Notes

**`runs-on` is not a label array.** A scale set matches on its name alone, so
the workflows use `runs-on: mbarukville`. The old `[self-hosted, mbarukville]`
form silently never matches and the job just sits queued. A classic runner
labelled `mbarukville` also matches the new form, so re-registering a normal
runner remains a working fallback.

**No Docker needed in the runner.** The image build happens on the Pi over ssh,
so the pod needs no dind and no `containerMode`.

**The key is copied, not linked.** A Secret volume cannot be 0600 for a non-root
user, and ssh rejects a group-readable key. The volume is mounted 0440 with
`fsGroup: 1001` so uid 1001 can read it, and the deploy action copies it to
`~/.ssh/id_pi_deploy` at 0600.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Job queued forever | `runs-on` mismatch, or the scale set failed to register — check controller logs |
| `rsync: command not found` | Running the stock image instead of the derived one |
| `Host key verification failed` | `known_hosts` missing from the secret |
| `Cannot read /etc/deploy-ssh/id_ed25519` | `fsGroup` missing, or `defaultMode` is 0400 rather than 0440 |
| `exec format error` | Image built for arm64; rebuild with `--platform linux/amd64` |
