# Pulp Admin Configuration Guide

Panduan lengkap konfigurasi semua plugin — untuk operator/admin.

**Target cluster**: `tbs-dev`
**Pulp API**: `https://pulp.dev.tbs.cloudeka.xyz/pulp/api/v3/`
**Pulp internal**: `http://localhost:24817` (dari dalam pod)

---

## Cara Menjalankan Script

Semua konfigurasi via Pulp REST API. Jalankan dari dalam pod:

```bash
# Satu baris
kubectl --kubeconfig kubeconfig/kubeconfig-tbs-dev.yaml \
  exec -n pulp deploy/pulp-api -- python3 -c "..."

# Dari file script di repo
kubectl --kubeconfig kubeconfig/kubeconfig-tbs-dev.yaml \
  exec -n pulp deploy/pulp-api -- python3 < scripts/pulp/<script>.py
```

> **Catatan**: Konfigurasi disimpan di PostgreSQL (bukan ConfigMap). Tidak ada GitOps untuk content config — perubahan dilakukan via API, didokumentasikan di script repo ini.

---

## Scripts yang Tersedia

| Script | Fungsi | Cara jalankan |
|---|---|---|
| `scripts/pulp/pulp-rpm-setup.py` | Setup/sync ulang RPM repos (rocky9-baseos, appstream, epel9). Tambah repo baru di `REPOS` list. | `python3 < scripts/pulp/pulp-rpm-setup.py` |
| `scripts/pulp/pulp-deb-setup.py` | Setup/sync ulang DEB repos (ubuntu-jammy, ubuntu-noble). Tambah repo di `REPOS`. ⚠️ Publish ~25 min. | `python3 < scripts/pulp/pulp-deb-setup.py` |
| `scripts/pulp/pulp-python-setup.py` | Tambah Python packages ke PyPI cache. Edit `PACKAGES` list. | `python3 < scripts/pulp/pulp-python-setup.py` |
| `scripts/pulp/pulp-ansible-setup.py` | Tambah Ansible collections. Edit `COLLECTIONS` list. ⚠️ Jangan hapus `requirements_file`. | `python3 < scripts/pulp/pulp-ansible-setup.py` |
| `scripts/pulp/pulp-container-status.py` | Cek pull-through registries. Tambah registry baru di `ADD_REGISTRY`. | `python3 < scripts/pulp/pulp-container-status.py` |

Semua script **idempotent** — aman dijalankan berulang.

---

## Pulp UI — Coverage

UI di `https://pulp.dev.tbs.cloudeka.xyz/ui/` hanya expose sebagian plugin:

| Plugin | Ada di UI? | Cara config |
|---|---|---|
| RPM | ✅ (menu "Pulp RPM" → RPMs) | UI bisa lihat packages; config via script |
| Ansible | ✅ (menu "Pulp Ansible" → Repositories, Remotes) | UI bisa sync, lihat collections |
| File | ✅ (menu "Pulp file") | Via UI |
| DEB | ❌ | Script saja |
| Python/PyPI | ❌ | Script saja |
| Container | ❌ | Script saja |
| HuggingFace | ❌ | Script saja |

### Status page — Storage "100%" adalah false alarm

Storage di UI menampilkan `100% — Total: 0 bytes, Free: 0 bytes`. Ini **bukan krisis** — backend RustFS (S3-compatible) tidak melaporkan kapasitas total ke Pulp API (`total: null`, `free: null`). UI salah render null sebagai 0. Data aktual yang terpakai ~1–2 GiB.

Cek real usage:
```bash
kubectl --kubeconfig kubeconfig/kubeconfig-tbs-dev.yaml \
  exec -n pulp deploy/pulp-api -- python3 -c "
import urllib.request, json, base64
H = {'Authorization': 'Basic ' + base64.b64encode(b'admin:<admin-password>').decode()}
with urllib.request.urlopen(urllib.request.Request('http://localhost:24817/pulp/api/v3/status/', headers=H)) as r:
    print(json.loads(r.read())['storage'])
"
# Output: {'total': null, 'used': 1110292962, 'free': null}
# 'used' dalam bytes — 1.1 GiB terpakai
```

---

## Sync Policy: `on_demand` vs `immediate`

Ganti di tiap script pada bagian `"policy"` saat create remote:

```python
"policy": "on_demand",   # default — metadata sync, file fetch saat dipakai
"policy": "immediate",   # download semua file saat sync
```

| | `on_demand` | `immediate` |
|---|---|---|
| Sync speed | Cepat | Lambat (download semua) |
| Storage setelah sync | Kecil (metadata only) | Besar (semua file) |
| Pull speed (client) | Fetch dari upstream pertama kali, lalu cache | Selalu dari cache lokal |
| Cocok untuk | Default — hemat storage | Air-gapped / no internet saat runtime |
| Estimasi storage RPM full | ~500 MB metadata | ~30–50 GB |
| Estimasi storage DEB full | ~1 GB metadata | ~200 GB+ |

> **Catatan Ansible**: `remotes/ansible/collection` **tidak support** `on_demand` — hanya `immediate` dan `streamed`. Default `immediate` (tapi ini metadata collections, bukan binary — aman).

**Cara ganti ke `immediate`** — edit baris di RPM script:
```python
# pulp-rpm-setup.py line ~79
"policy": "immediate",   # ganti dari on_demand

# pulp-deb-setup.py line ~83
"policy": "immediate",
```

---

## Helper Functions (gunakan di semua script)

```python
import urllib.request, json, base64, time

BASE = "http://localhost:24817"
H = {
    "Authorization": "Basic " + base64.b64encode(b"admin:<admin-password>").decode(),
    "Content-Type": "application/json",
}

def get(path):
    with urllib.request.urlopen(urllib.request.Request(BASE + path, headers=H)) as r:
        return json.loads(r.read())

def post(path, data):
    body = json.dumps(data).encode()
    with urllib.request.urlopen(urllib.request.Request(BASE + path, data=body, headers=H, method="POST")) as r:
        return json.loads(r.read())

def patch(path, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(BASE + path, data=body, headers=H, method="PATCH")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def wait(task_href, interval=5, max_attempts=120):
    for i in range(max_attempts):
        d = get(task_href)
        if d["state"] == "completed": return True
        if d["state"] == "failed":
            print("FAIL:", d.get("error", {})); return False
        if i % 6 == 0: print(f"  [{i*interval}s] {d['state']}")
        time.sleep(interval)
    print("TIMEOUT"); return False
```

---

## Plugin 1 — Container Registry (Pull-Through)

### Konsep

Pull-through cache — tidak perlu sync. Setiap `docker pull` ke Pulp URL langsung fetch dari upstream dan cache ke RustFS.

### Cara kerja

```
docker pull pulp.dev.tbs.cloudeka.xyz/dockerhub/library/nginx:latest
                    ↓
          Pulp ContainerPullThroughDistribution
                    ↓
          Docker Hub (upstream) — fetch + cache
                    ↓
          RustFS bucket pulp-content
```

### Cek konfigurasi saat ini

```python
# Lihat semua pull-through distributions
dists = get("/pulp/api/v3/distributions/container/pull-through/")["results"]
for d in dists:
    print(f"  {d['name']:20} base_path={d['base_path']}  remote={d.get('remote','?')}")
```

### Tambah registry baru

```python
# Contoh: tambah GHCR
remote = post("/pulp/api/v3/remotes/container/pull-through/", {
    "name": "ghcr",
    "url": "https://ghcr.io",
    # Jika butuh auth:
    # "username": "...",
    # "password": "...",
})
t = post("/pulp/api/v3/distributions/container/pull-through/", {
    "name": "ghcr",
    "base_path": "ghcr",
    "remote": remote["pulp_href"],
})
wait(t["task"])
print("ghcr pull-through LIVE")
# Usage: docker pull pulp.dev.tbs.cloudeka.xyz/ghcr/<image>:<tag>
```

### Registry yang sudah dikonfigurasi

| Name | Base Path | Upstream | Auth |
|---|---|---|---|
| dockerhub | `dockerhub/` | `https://index.docker.io` | Anonymous |
| quay | `quay/` | `https://quay.io` | Anonymous |

### Tambah auth untuk registry private

```python
# Update remote yang sudah ada dengan credentials
remote = get("/pulp/api/v3/remotes/container/pull-through/?name=ghcr")["results"][0]
patch(remote["pulp_href"], {
    "username": "myuser",
    "password": "ghp_xxxxxxxxxxxx",
})
print("credentials updated")
```

---

## Plugin 2 — RPM (Rocky Linux + EPEL)

### Konsep

`on_demand` policy — metadata sync dulu, file `.rpm` fetch dari upstream saat `dnf install`.

### Cek status

```python
repos = get("/pulp/api/v3/repositories/rpm/rpm/")["results"]
for r in repos:
    ver = r["latest_version_href"].split("/")[-2]
    print(f"  {r['name']:25} version={ver}")

dists = get("/pulp/api/v3/distributions/rpm/rpm/")["results"]
for d in dists:
    print(f"  {d['name']:25} base_path={d['base_path']} pub={bool(d.get('publication'))}")
```

### Sync ulang (refresh metadata)

```python
repos = get("/pulp/api/v3/repositories/rpm/rpm/")["results"]
for repo in repos:
    print(f"Syncing {repo['name']}...")
    t = post(repo["pulp_href"] + "sync/", {})
    if not wait(t["task"]): continue

    # Publish
    ver = get(repo["pulp_href"])["latest_version_href"]
    pub_task = post("/pulp/api/v3/publications/rpm/rpm/", {"repository_version": ver})
    wait(pub_task["task"])
    pub_href = get(f"/pulp/api/v3/publications/rpm/rpm/?repository_version={ver}")["results"][0]["pulp_href"]

    # Link dist
    dists = get("/pulp/api/v3/distributions/rpm/rpm/")["results"]
    dist = next((d for d in dists if d["name"] == repo["name"]), None)
    if dist:
        patch(dist["pulp_href"], {"repository": None, "publication": pub_href})
        print(f"  {repo['name']} updated")
```

### Tambah repo RPM baru

```python
name = "centos-stream9"
url  = "https://mirror.stream.centos.org/9-stream/BaseOS/x86_64/os/"

remote = post("/pulp/api/v3/remotes/rpm/rpm/", {
    "name": name, "url": url, "policy": "on_demand"
})
repo = post("/pulp/api/v3/repositories/rpm/rpm/", {
    "name": name, "remote": remote["pulp_href"]
})
wait(post(repo["pulp_href"] + "sync/", {})["task"])

ver = get(repo["pulp_href"])["latest_version_href"]
pub_task = post("/pulp/api/v3/publications/rpm/rpm/", {"repository_version": ver})
wait(pub_task["task"])
pub_href = get(f"/pulp/api/v3/publications/rpm/rpm/?repository_version={ver}")["results"][0]["pulp_href"]

dist_task = post("/pulp/api/v3/distributions/rpm/rpm/", {
    "name": name, "base_path": f"rpm/{name}", "publication": pub_href
})
wait(dist_task["task"])
print(f"RPM {name} LIVE — https://pulp.dev.tbs.cloudeka.xyz/pulp/content/rpm/{name}/")
```

---

## Plugin 3 — DEB (Ubuntu)

### Konsep

`on_demand` policy. Sync metadata → publish (generate apt index) → distribute.

> ⚠️ DEB publish lama — ubuntu-noble ~25 menit. Jangan timeout script di bawah 30 menit.

### Cek status

```python
repos = get("/pulp/api/v3/repositories/deb/apt/")["results"]
for r in repos:
    ver = r["latest_version_href"].split("/")[-2]
    print(f"  {r['name']:25} version={ver}")

dists = get("/pulp/api/v3/distributions/deb/apt/")["results"]
for d in dists:
    print(f"  {d['name']:25} pub={bool(d.get('publication'))}")
```

### Sync ulang (refresh metadata)

```python
# Ganti interval wait ke 10s, max 360 untuk DEB publish yang lama
def wait_long(task_href):
    for i in range(360):
        d = get(task_href)
        if d["state"] == "completed": return True
        if d["state"] == "failed": print("FAIL:", d.get("error",{})); return False
        if i % 6 == 0: print(f"  [{i*10}s] {d['state']}")
        time.sleep(10)

repos = get("/pulp/api/v3/repositories/deb/apt/")["results"]
for repo in repos:
    print(f"Syncing {repo['name']}...")
    wait(post(repo["pulp_href"] + "sync/", {})["task"])

    ver = get(repo["pulp_href"])["latest_version_href"]
    print(f"  Publishing (may take 25+ min)...")
    pub_task = post("/pulp/api/v3/publications/deb/apt/", {
        "repository_version": ver, "simple": True
    })
    wait_long(pub_task["task"])
    pub_href = get(f"/pulp/api/v3/publications/deb/apt/?repository_version={ver}")["results"][0]["pulp_href"]

    dists = get("/pulp/api/v3/distributions/deb/apt/")["results"]
    dist = next((d for d in dists if d["name"] == repo["name"]), None)
    if dist:
        patch(dist["pulp_href"], {"repository": None, "publication": pub_href})
        print(f"  {repo['name']} updated")
```

### Tambah repo DEB baru

```python
name  = "ubuntu-focal"
url   = "http://archive.ubuntu.com/ubuntu"
dists_ubuntu = "focal"
comps = "main restricted universe multiverse"
arch  = "amd64"

remote = post("/pulp/api/v3/remotes/deb/apt/", {
    "name": name, "url": url, "distributions": dists_ubuntu,
    "components": comps, "architectures": arch, "policy": "on_demand"
})
repo = post("/pulp/api/v3/repositories/deb/apt/", {
    "name": name, "remote": remote["pulp_href"]
})
wait(post(repo["pulp_href"] + "sync/", {})["task"])

ver = get(repo["pulp_href"])["latest_version_href"]
pub_task = post("/pulp/api/v3/publications/deb/apt/", {
    "repository_version": ver, "simple": True
})
wait_long(pub_task["task"])
pub_href = get(f"/pulp/api/v3/publications/deb/apt/?repository_version={ver}")["results"][0]["pulp_href"]

dist_task = post("/pulp/api/v3/distributions/deb/apt/", {
    "name": name, "base_path": f"deb/{name}", "publication": pub_href
})
wait(dist_task["task"])
print(f"DEB {name} LIVE — https://pulp.dev.tbs.cloudeka.xyz/pulp/content/deb/{name}/")
```

---

## Plugin 4 — Python / PyPI

### Konsep

**Tidak ada pull-through** — hanya package dalam `includes` list yang tersedia. Tambah package baru = update list + sync ulang.

### Script idempotent (gunakan ini)

```bash
kubectl --kubeconfig kubeconfig/kubeconfig-tbs-dev.yaml \
  exec -n pulp deploy/pulp-api -- python3 < scripts/pulp/pulp-python-setup.py
```

Edit `PACKAGES` list di `scripts/pulp/pulp-python-setup.py`, commit, jalankan.

### Tambah package baru (manual)

```python
# Cek list saat ini
remote = get("/pulp/api/v3/remotes/python/python/")["results"][0]
current = [i["name"] for i in remote.get("includes", [])]
print("current:", current)

# Tambah package
new = ["flask", "fastapi", "uvicorn"]
merged = sorted(set(current) | set(new))
patch(remote["pulp_href"], {"includes": [{"name": p} for p in merged]})

# Sync → publish → link
repo = get("/pulp/api/v3/repositories/python/python/")["results"][0]
wait(post(repo["pulp_href"] + "sync/", {})["task"])
ver = get(repo["pulp_href"])["latest_version_href"]
pub_task = post("/pulp/api/v3/publications/python/python/", {"repository_version": ver})
wait(pub_task["task"])
pub_href = get(f"/pulp/api/v3/publications/python/python/?repository_version={ver}")["results"][0]["pulp_href"]
dist = get("/pulp/api/v3/distributions/python/pypi/")["results"][0]
patch(dist["pulp_href"], {"repository": None, "publication": pub_href})
print("pypi updated:", merged)
```

### Hapus package dari list

```python
remote = get("/pulp/api/v3/remotes/python/python/")["results"][0]
current = [i["name"] for i in remote.get("includes", [])]
remove = ["flask"]  # package yang mau dihapus
updated = [p for p in current if p not in remove]
patch(remote["pulp_href"], {"includes": [{"name": p} for p in updated]})
# Lalu sync ulang
```

---

## Plugin 5 — Ansible Galaxy

### Konsep

**Tidak ada pull-through** — hanya collections dalam `requirements_file` yang tersedia. Tidak ada publish step untuk Ansible (dist langsung serve dari repo).

> ⚠️ **Jangan hapus `requirements_file`** — sync tanpa requirements = download seluruh galaxy.ansible.com = OOM.

### Script idempotent (gunakan ini)

```bash
kubectl --kubeconfig kubeconfig/kubeconfig-tbs-dev.yaml \
  exec -n pulp deploy/pulp-api -- python3 < scripts/pulp/pulp-ansible-setup.py
```

Edit `COLLECTIONS` list di `scripts/pulp/pulp-ansible-setup.py`, commit, jalankan.

### Tambah collection baru (manual)

```python
def parse_req(yaml_str):
    cols = []
    for line in (yaml_str or "").splitlines():
        line = line.strip()
        if line.startswith("- name:"):
            cols.append(line.split("- name:")[1].strip())
    return cols

def build_req(col_list):
    return "collections:\n" + "".join(f"  - name: {c}\n" for c in sorted(col_list))

remote = get("/pulp/api/v3/remotes/ansible/collection/")["results"][0]
current = parse_req(remote.get("requirements_file", ""))
print("current:", current)

new = ["community.kubernetes", "ansible.utils"]
merged = sorted(set(current) | set(new))
patch(remote["pulp_href"], {"requirements_file": build_req(merged)})

repo = get("/pulp/api/v3/repositories/ansible/ansible/")["results"][0]
wait_task = post(repo["pulp_href"] + "sync/", {})
# wait dengan interval lebih panjang untuk ansible
for _ in range(360):
    d = get(wait_task["task"])
    if d["state"] == "completed": break
    if d["state"] == "failed": print("FAIL"); break
    time.sleep(10)
print("ansible updated:", merged)
```

---

## Plugin 6 — HuggingFace Hub

### Konsep

On-demand proxy — `remote` dipasang langsung di distribution (bukan di repository). Tidak ada sync diperlukan. Setiap request model langsung proxy ke huggingface.co.

### Cek status

```python
dists = get("/pulp/api/v3/distributions/hugging_face/hugging-face/")["results"]
for d in dists:
    print(f"  {d['name']:20} base_path={d['base_path']} remote={bool(d.get('remote'))}")
```

### Konfigurasi ulang / tambah model cache spesifik

```python
# Cek remote yang ada
remote = get("/pulp/api/v3/remotes/hugging_face/hugging-face/")["results"][0]
print("remote:", remote["pulp_href"])

# Dist sudah ada dengan remote — tidak perlu update apa-apa
# Untuk request model: gunakan endpoint langsung
# curl -u admin:<admin-password> https://pulp.dev.tbs.cloudeka.xyz/pulp/content/huggingface/api/models/gpt2
```

---

## Cek Status Semua Plugin Sekaligus

```python
import urllib.request, json, base64
BASE = "http://localhost:24817"
H = {"Authorization": "Basic " + base64.b64encode(b"admin:<admin-password>").decode()}

def get(p):
    with urllib.request.urlopen(urllib.request.Request(BASE+p, headers=H)) as r:
        return json.loads(r.read())

print("=== TASKS ===")
for s in ["running", "waiting", "failed"]:
    print(f"  {s}: {get(f'/pulp/api/v3/tasks/?state={s}')['count']}")

print("\n=== DISTRIBUTIONS ===")
checks = [
    ("container", "/pulp/api/v3/distributions/container/pull-through/"),
    ("rpm",       "/pulp/api/v3/distributions/rpm/rpm/"),
    ("deb",       "/pulp/api/v3/distributions/deb/apt/"),
    ("python",    "/pulp/api/v3/distributions/python/pypi/"),
    ("ansible",   "/pulp/api/v3/distributions/ansible/ansible/"),
    ("hf",        "/pulp/api/v3/distributions/hugging_face/hugging-face/"),
]
for label, path in checks:
    results = get(path)["results"]
    if not results:
        print(f"  {label:10} MISSING")
        continue
    for d in results:
        status = "remote=YES" if d.get("remote") else ("pub=YES" if d.get("publication") else "pub=NO")
        print(f"  {label:10} {d['base_path']:35} {status}")

print("\n=== REPO VERSIONS ===")
for rtype in ["rpm/rpm", "deb/apt", "python/python", "ansible/ansible"]:
    repos = get(f"/pulp/api/v3/repositories/{rtype}/")["results"]
    for r in repos:
        ver = r["latest_version_href"].split("/")[-2]
        print(f"  [{r['name']}] v{ver}")

print("\n=== DB SIZE ===")
# Run from pulp-api pod — uses Django ORM
```

---

## Maintenance

### Orphan cleanup (hapus konten tidak terpakai)

```python
# Jalankan setelah sync gagal atau hapus repo
t = post("/pulp/api/v3/orphans/cleanup/", {"orphan_protection_time": 0})
# Tunggu — bisa 30-60 menit jika banyak orphan
for _ in range(360):
    d = get(t["task"])
    if d["state"] == "completed":
        for p in d.get("progress_reports", []):
            print(f"  {p['code']}: {p['done']} deleted")
        break
    time.sleep(10)
```

### pgbackrest — manual base backup

```bash
kubectl --kubeconfig kubeconfig/kubeconfig-tbs-dev.yaml \
  exec -n prod db-pulp-repo-host-0 -c pgbackrest -- \
  pgbackrest backup --stanza=db --type=full
```

Jalankan setelah sync besar (banyak data baru masuk) untuk mengaktifkan WAL pruning.

### Cek disk DB

```bash
kubectl --kubeconfig kubeconfig/kubeconfig-tbs-dev.yaml \
  exec -n prod db-pulp-instance1-dqbq-0 -c database -- df -h /pgdata
```

---

## Ringkasan: Mana yang Butuh Tindakan Admin?

| Plugin | User pull otomatis? | Admin perlu tindakan jika... |
|---|---|---|
| Container | ✅ Ya | Tambah registry baru / tambah credentials |
| RPM | ✅ Ya | Sync ulang untuk refresh metadata (mis. paket baru di upstream) |
| DEB | ✅ Ya | Sync ulang untuk refresh metadata |
| Python | ❌ Tidak | User minta package baru → tambah ke PACKAGES + jalankan script |
| Ansible | ❌ Tidak | User minta collection baru → tambah ke COLLECTIONS + jalankan script |
| HuggingFace | ✅ Ya | Tidak perlu tindakan — proxy otomatis |
