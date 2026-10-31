#!/usr/bin/env python3
"""Docker Hub'daki en son SHA imajlarını göster; istenirse yerel YAML'lara yaz."""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
FILES = {
    "discovery-server": "dependencies/eureka.yaml",
    "api-gateway": "services/gateway.yaml",
    "frontend": "services/frontend.yaml",
    **{name: "services/bank-services.yaml" for name in (
        "backend", "auth-service", "bill-service", "corporate-service",
        "currency-service", "notification-service",
    )},
}
TAG = re.compile(r"sha-[0-9a-f]{40}\Z")


def parse_timestamp(value):
    # Python 3.10 fromisoformat her kesir uzunluğunu kabul etmez. Docker Hub
    # 1–6 basamaklı saniye kesri veya kesirsiz UTC zaman döndürebilir.
    value = value.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise ValueError(f"Geçersiz veya desteklenmeyen push tarihi: {value}")


def latest_tag(repository):
    url = f"https://hub.docker.com/v2/namespaces/mstfckrc/repositories/{repository}/tags?page_size=100"
    candidates = []
    visited = set()
    while url:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "hub.docker.com" or url in visited:
            raise ValueError(f"{repository}: geçersiz veya döngüsel sayfa adresi")
        visited.add(url)
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "bank-demo-kubernetes/1.0"})
        with urlopen(request, timeout=15) as response:
            page = json.load(response)
        for item in page["results"]:
            name = item.get("name", "")
            if not TAG.fullmatch(name):
                continue
            timestamp = item.get("tag_last_pushed") or item.get("last_updated")
            if not timestamp:
                continue
            images = item.get("images") or []
            if not any(x.get("os") == "linux" and x.get("architecture") == "amd64" for x in images):
                continue
            time = parse_timestamp(timestamp)
            candidates.append((time, name))
        url = page.get("next")
    if not candidates:
        raise ValueError(f"{repository}: tarihli, linux/amd64 uyumlu SHA etiketi bulunamadı")
    latest_time = max(time for time, _ in candidates)
    latest = {name for time, name in candidates if time == latest_time}
    if len(latest) != 1:
        raise ValueError(f"{repository}: aynı tarihte birden fazla son etiket bulundu")
    return next(iter(latest)), latest_time


def prepare_changes(selected):
    """YAML biçimini koruyarak yalnız beklenen imaj satırlarını hazırla."""
    originals = {path: (ROOT / path).read_text() for path in set(FILES.values())}
    updated = dict(originals)
    changes = []
    for name, path in FILES.items():
        tag, when = selected[name]
        pattern = re.compile(rf"(?m)^([ \t]*image:[ \t]*)docker\.io/mstfckrc/{re.escape(name)}:sha-[0-9a-f]{{40}}(?:@sha256:[0-9a-f]{{64}})?([ \t]*)$")
        matches = list(pattern.finditer(updated[path]))
        if len(matches) != 1:
            raise ValueError(f"{path}: {name} için tek bir beklenen imaj satırı bulunamadı")
        old = matches[0].group(0).strip().split()[-1]
        new = f"docker.io/mstfckrc/{name}:{tag}"
        updated[path] = pattern.sub(lambda m: m.group(1) + new + m.group(2), updated[path], count=1)
        changes.append((name, old, new, when))
    return originals, updated, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="seçilen imajları yalnızca yerel YAML dosyalarına yaz")
    args = parser.parse_args()
    try:
        # Önce bütün repository'leri oku; herhangi biri başarısızsa dosya değiştirme.
        selected = {name: latest_tag(name) for name in FILES}
        originals, updated, changes = prepare_changes(selected)
        for name, old, new, when in changes:
            print(f"{name}: {old} -> {new} ({when.isoformat()})")
        if args.write:
            for path, content in updated.items():
                if content != originals[path]:
                    (ROOT / path).write_text(content)
            print("Yerel YAML dosyaları güncellendi. Cluster'a uygulanmadı.")
        else:
            print("Kontrol tamamlandı; dosya değiştirilmedi. Yazmak için --write kullanın.")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"İmaj seçimi/güncellemesi başarısız: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
