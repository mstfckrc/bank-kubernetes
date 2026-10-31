# Bank-Demo Kubernetes

Bank-Demo mikroservislerini K3s üzerinde çalıştırmak için kullanılan Kubernetes manifestleri. Bu depo; uygulama, veritabanı, bağımlılıklar, Ingress ve izleme yapılandırmasını içerir. Uygulamanın kaynak kodu ve Docker imajlarının oluşturulması ayrı [Bank-Microservice](https://github.com/mstfckrc/Bank-Microservice) deposundadır.

Bu kurulum, Kubernetes ve dağıtık uygulamaları deneyerek öğrenmek için hazırlanmış bir laboratuvardır. Tek control-plane ve iki worker kullanır; üretim için yüksek erişilebilirlik iddiası taşımaz.

```text
Tarayıcı → ingress-nginx → frontend Service → frontend → API Gateway → banka servisleri
Banka servisleri → PostgreSQL → Longhorn volume
```

Banka servisleri ihtiyaçlarına göre Keycloak, Eureka, RabbitMQ ve Redis gibi bağımlılıklarla da iletişim kurar. Uygulama iş yükleri worker node'lara yerleşir. PostgreSQL veritabanları birer Pod kullanır; Longhorn volume'ları iki kopya tutacak şekilde yapılandırılmıştır. Volume replikasyonu, veritabanı replikasyonu veya yedekleme yerine geçmez.

## Dizinler

| Yol | İçerik |
| --- | --- |
| `config/` | Namespace, uygulama ayarları, Secret örnekleri, CoreDNS ve Longhorn chart tanımı |
| `data/` | PostgreSQL StatefulSet, Service ve StorageClass tanımları |
| `dependencies/` | Keycloak, Eureka, RabbitMQ, Redis ve Keycloak realm dosyası |
| `services/` | Banka servisleri, API Gateway ve frontend |
| `ingress/` | Uygulama, Eureka ve Longhorn Ingress kuralları |
| `monitoring/` | Prometheus, Grafana, node-exporter, Elasticsearch, Logstash ve Kibana |
| `images.py` | Docker Hub uygulama imajı etiketlerini inceleyen ve güncelleyen betik |

## Ortam ve ön koşullar

Önceki cluster kontrollerinde K3s `v1.35.5+k3s1`, ingress-nginx `v1.10.1` ve Longhorn `1.12.1` kullanılıyordu. Bu bilgiler gözlenen ortamı anlatır; farklı sürümler için uyumluluk garantisi değildir. Longhorn sürümü [config/longhorn.yaml](config/longhorn.yaml) içinde sabitlenmiştir.

- K3s ve `kubectl` erişimi hazır olmalı. Longhorn kurulumu K3s Helm Controller kullanır; ayrıca Helm CLI gerekmez.
- `ingress-nginx` controller ve LoadBalancer erişimi önceden kurulmuş olmalı. Bu depo controller'ı kurmaz.
- Longhorn'un node gereksinimleri ve iki worker'da uygun depolama alanı sağlanmalı. Bu kurulumda varsayılan disk yalnızca `node.longhorn.io/create-default-disk=true` etiketli node'larda oluşturulur. [Longhorn gereksinimleri](https://longhorn.io/docs/1.12.1/deploy/install/) ve [disk etiketi](https://longhorn.io/kb/tip-only-use-storage-on-a-set-of-nodes/) ayrıntıları için resmi belgelere bakın.
- İstemcide kullanılan `*.bank.local` host adları ingress-nginx LoadBalancer adresine çözülmeli. `config/coredns-custom.yaml` içindeki `auth.bank.local` IP'si de bu adresle eşleşmeli.
- TLS sertifikası kullanılan host adlarını kapsamalı. Sertifikayı imzalayan CA, istemcide güvenilir olmalı ve frontend için kullanılan `config/root-ca.pem` ile eşleşmeli. Repodaki dosya CA sertifikasıdır; özel anahtarı içermez.

## İlk kurulum

Komutları depo kökünden çalıştırın. Bu adımlar mevcut lab kurulumuna göre hazırlanmıştır; yeni bir cluster üzerinde baştan sona tekrar doğrulanmış bir kurulum testi değildir. Her aşamada kaynakların hazır olduğunu kontrol edin.

### 1. Namespace ve Longhorn

Worker adlarını kendi ortamınıza göre seçin. Etiketleme, Longhorn yeni node'larda varsayılan disk oluştururken kullanılır; mevcut disk yapılandırmasını yeniden oluşturmaz.

```bash
kubectl apply -f config/namespace.yaml
kubectl label node k8s-worker1 k8s-worker2 node.longhorn.io/create-default-disk=true --overwrite
kubectl apply -f config/longhorn.yaml
kubectl get pods -n longhorn-system
kubectl get nodes.longhorn.io -n longhorn-system
```

Devam etmeden önce Longhorn bileşenlerinin ve iki worker disklerinin hazır/schedulable olduğundan emin olun. `config/longhorn.yaml` chart'ı arka planda kurar; bu işlem biraz sürebilir.

### 2. Ayarlar ve Secret'lar

Örnekleri yerel dosyalara kopyalayın ve değerleri doldurun. `cp -n`, mevcut yerel dosyanın üzerine yazmaz. Realm client secret değerleri ile uygulama servislerinin kullandığı değerler aynı olmalıdır; reCAPTCHA değeri ilgili sağlayıcıdan alınır.

```bash
cp -n config/secrets.example.yaml config/bank-demo-secrets.yaml
cp -n config/grafana-secret.example.yaml config/grafana-secret.yaml
```

Gerçek `config/bank-demo-secrets.yaml`, `config/grafana-secret.yaml` ve varsa `config/tls-secrets.yaml` Git tarafından ignore edilir. Örnek dosyaları doğrudan uygulamayın. `kubectl apply -f config/` de kullanmayın: dizinde hem örnekler hem de yerel Secret dosyaları bulunabilir.

TLS sertifikası ve anahtarı yerel dosyalardaysa, yolları kendi ortamınıza göre belirleyip Secret'ı üç namespace için oluşturun. Mevcut yerel `config/tls-secrets.yaml` dosyanız zaten hazırlanmışsa bunun yerine onu uygulayabilirsiniz.

```bash
TLS_CERT=/guvenli/yol/bank-local.crt
TLS_KEY=/guvenli/yol/bank-local.key
for ns in default bank-demo longhorn-system; do
  kubectl create secret tls bank-local-tls -n "$ns" \
    --cert="$TLS_CERT" --key="$TLS_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -
done
```

Ingress IP'sini `config/coredns-custom.yaml` içinde kontrol ettikten sonra ayarları ve Secret'ları uygulayın. Realm, CA ve dashboard ConfigMap'leri depodaki dosyalardan oluşturulur:

```bash
kubectl apply -f config/config.yaml
kubectl apply -f config/coredns-custom.yaml
kubectl apply -f config/bank-demo-secrets.yaml
kubectl apply -f config/grafana-secret.yaml
kubectl create configmap bank-root-ca -n bank-demo --from-file=root-ca.pem=config/root-ca.pem --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap bank-realm -n bank-demo --from-file=bank-realm-realm.json=dependencies/realm.json --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap bank-grafana-dashboards -n default --from-file=monitoring/dashboards --dry-run=client -o yaml | kubectl apply --server-side -f -
```

Dashboard ConfigMap'i büyük olduğu için son komutta server-side apply kullanılır. Keycloak realm'i başlangıçta içe aktarır; realm veritabanında zaten varsa dosyadaki değişiklikler kendiliğinden uygulanmaz.

### 3. Veri ve servisler

Önce veritabanlarını oluşturun; beş PostgreSQL Pod'u `1/1 Running` ve PVC'leri `Bound` olana kadar bekleyin:

```bash
kubectl apply -f data/postgres.yaml
kubectl get pods,pvc -n bank-demo
```

Sonra bağımlılıkları başlatın. Keycloak hazır olduktan sonra uygulama servislerine geçin:

```bash
kubectl apply -f monitoring/logging.yaml
kubectl apply -f dependencies/messaging.yaml
kubectl apply -f dependencies/eureka.yaml
kubectl apply -f dependencies/keycloak.yaml
kubectl rollout status deployment/keycloak -n bank-demo --timeout=8m
```

```bash
kubectl apply -f services/bank-services.yaml
kubectl apply -f services/gateway.yaml
kubectl apply -f services/frontend.yaml
kubectl apply -f monitoring/monitoring.yaml
kubectl apply -f ingress/ingress.yaml
```

Grafana ve Prometheus `default`, banka servisleri ve loglama `bank-demo`, Longhorn `longhorn-system` namespace'indedir.

## Kontrol ve erişim

```bash
kubectl get deployments,statefulsets,pods,pvc -n bank-demo
kubectl get deployment,pods,pvc -n default
kubectl get ingress -n bank-demo
kubectl get ingress -n default
```

Uygulama ve veritabanı Pod'larında `READY 1/1`, PVC'lerde `Bound` beklenir. Başlangıç sırasında geçici `ContainerCreating` veya `0/1` görülebilir. Bir Pod hazır kalmıyorsa `kubectl describe pod <pod-adı> -n <namespace>` ve `kubectl logs <pod-adı> -n <namespace>` ile nedeni incelenebilir.

| Adres | Bileşen |
| --- | --- |
| `https://app.bank.local` | Bank-Demo frontend |
| `https://auth.bank.local` | Keycloak |
| `https://api.bank.local`, `https://docs.bank.local` | API Gateway |
| `https://eureka.bank.local` | Eureka |
| `https://monitor.bank.local` | Grafana |
| `https://longhorn.bank.local` | Longhorn |

İlk banka yöneticisi, auth-service başlangıcında bootstrap Secret'ındaki bilgilerle oluşturulur. Mevcut kullanıcıların veya veritabanlarının parolasını değiştirmek için yalnızca Secret'ı değiştirmek yeterli değildir.

## Uygulama imajlarını güncelleme

```bash
python3 images.py
python3 images.py --write
git diff
```

İlk komut Docker Hub'daki uygun Linux/amd64 SHA etiketlerini gösterir. `--write`, seçilen etiketleri yerel manifestlere yazar. Her servis için en yeni etiket ayrı seçilir; betik ortak bir sürümün uyumluluğunu doğrulamaz. Değişiklikleri inceledikten sonra ilgili manifestleri `kubectl apply` ile uygulayın. Docker Hub'a yeni imaj gönderilmesi tek başına Kubernetes rollout'u başlatmaz.

## Veri ve temizlik

PostgreSQL ve Grafana PVC'leri Longhorn'da iki kopyalı, `Retain` politikasıyla oluşturulur. PVC silinse bile PV ve Longhorn volume'u kalabilir. Prometheus ve Elasticsearch verileri kalıcı PVC kullanmaz. Temizlik veya yeniden kurulumdan önce mevcut PV/PVC ve Longhorn volume'larını ayrıca değerlendirin.

## Kaynaklar

- [Kubernetes: workload yönetimi ve kubectl apply](https://kubernetes.io/docs/concepts/workloads/management/)
- [K3s: Helm Controller](https://docs.k3s.io/helm)
- [Keycloak: realm içe ve dışa aktarma](https://www.keycloak.org/server/importExport)
