# ⚡ QUICK START - OPTIMISATIONS IMMÉDIATES

## 🚀 INSTALLATION EN 30 MINUTES

### 1️⃣ Gunicorn + Workers (5 minutes)

**requirements.txt** - Ajouter:
```
gunicorn==21.2.0
gevent==23.9.1
```

**Dockerfile** - Remplacer la dernière ligne:
```dockerfile
# Avant:
# CMD ["python", "docker-entrypoint.py"]

# Après:
CMD ["sh", "-c", "python docker-entrypoint.py && gunicorn octascraper.wsgi:application --bind 0.0.0.0:8000 --workers 4 --worker-class gevent --worker-connections 1000 --timeout 120 --keep-alive 5 --max-requests 1000 --max-requests-jitter 50"]
```

**Explication:**
- `--workers 4` = 4 processus parallèles
- `--worker-class gevent` = async I/O optimisé
- `--worker-connections 1000` = 1000 connexions simultanées par worker
- `--max-requests 1000` = redémarre worker après 1000 req (évite memory leaks)

---

### 2️⃣ Redis Cache (10 minutes)

**requirements.txt** - Ajouter:
```
django-redis==5.4.0
redis==5.0.1
hiredis==2.3.2
```

**docker-compose.yml** - Ajouter service Redis:
```yaml
services:
  redis:
    image: redis:7-alpine
    restart: always
    command: >
      redis-server
      --maxmemory 512mb
      --maxmemory-policy allkeys-lru
      --appendonly yes
      --save 900 1
      --save 300 10
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  web:
    # ... existing config ...
    depends_on:
      redis:
        condition: service_healthy
      db:
        condition: service_healthy

volumes:
  redis_data:
  mysql_data:
```

**octascraper/settings.py** - Ajouter après DATABASES:
```python
# ══════════════════════════════════════════════════════════
# REDIS CACHE CONFIGURATION
# ══════════════════════════════════════════════════════════
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://redis:6379/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'PARSER_CLASS': 'redis.connection.HiredisParser',
            'CONNECTION_POOL_KWARGS': {
                'max_connections': 50,
                'retry_on_timeout': True,
            },
            'SOCKET_CONNECT_TIMEOUT': 5,
            'SOCKET_TIMEOUT': 5,
            'COMPRESSOR': 'django_redis.compressors.zlib.ZlibCompressor',
            'IGNORE_EXCEPTIONS': True,  # Don't crash if Redis down
        },
        'KEY_PREFIX': 'octahub',
        'TIMEOUT': 300,  # 5 minutes default
    }
}

# Use Redis for sessions (much faster than DB)
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_CACHE_ALIAS = 'default'
SESSION_COOKIE_AGE = 86400  # 24 hours
```

---

### 3️⃣ MySQL Connection Pooling (3 minutes)

**octascraper/settings.py** - Modifier DATABASES:
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DB_NAME', 'octahub'),
        'USER': os.environ.get('DB_USER', 'root'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'octahub2003'),
        'HOST': os.environ.get('DB_HOST', 'db'),
        'PORT': os.environ.get('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES', innodb_strict_mode=1",
            'isolation_level': 'read committed',
        },
        # ✨ CONNECTION POOLING ✨
        'CONN_MAX_AGE': 600,  # Keep connections alive 10 minutes
        'CONN_HEALTH_CHECKS': True,  # Test connection before reuse
        'ATOMIC_REQUESTS': True,  # Transaction per request
    }
}
```

---

### 4️⃣ Optimiser views.py avec Cache (5 minutes)

**core/views.py** - Ajouter en haut:
```python
from django.core.cache import cache
from django.views.decorators.cache import cache_page
import hashlib
```

**Modifier get_sitemap_urls:**
```python
def get_sitemap_urls(base_url, session):
    """Tente de récupérer les URLs depuis le sitemap.xml (avec cache)"""
    # Cache key based on domain
    cache_key = f'sitemap_{hashlib.md5(base_url.encode()).hexdigest()}'
    
    # Try cache first
    cached_urls = cache.get(cache_key)
    if cached_urls is not None:
        return cached_urls
    
    sitemap_urls = []
    possible_sitemaps = [
        f"{base_url}/sitemap.xml",
        f"{base_url}/sitemap_index.xml",
        f"{base_url}/sitemap-index.xml",
        f"{base_url}/sitemap/sitemap.xml"
    ]
    
    for sitemap_url in possible_sitemaps:
        try:
            response = session.get(sitemap_url, timeout=5, verify=False)
            if response.status_code == 200:
                urls = re.findall(r'<loc>(.*?)</loc>', response.text)
                sitemap_urls.extend(urls[:50])
                if sitemap_urls:
                    break
        except:
            continue
    
    final_urls = list(set(sitemap_urls))
    
    # Cache for 1 hour (sitemaps change rarely)
    cache.set(cache_key, final_urls, 3600)
    
    return final_urls
```

**Ajouter cache pour pages statiques:**
```python
# Cache landing page for 1 hour
@cache_page(60 * 60)
def landing(request):
    return render(request, 'core/landing.html')

# Cache terms/privacy for 24 hours
@cache_page(60 * 60 * 24)
def terms(request):
    return render(request, 'core/terms.html')

@cache_page(60 * 60 * 24)
def privacy(request):
    return render(request, 'core/privacy.html')
```

---

### 5️⃣ Compression & Static Files (5 minutes)

**octascraper/settings.py** - Modifier:
```python
# ══════════════════════════════════════════════════════════
# STATIC FILES WITH COMPRESSION
# ══════════════════════════════════════════════════════════
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = []

# Whitenoise with compression
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Whitenoise settings
WHITENOISE_COMPRESS_OFFLINE = True
WHITENOISE_COMPRESS_OFFLINE_MANIFEST = 'staticfiles.json'
WHITENOISE_MAX_AGE = 31536000  # 1 year cache for static files
WHITENOISE_ALLOW_ALL_ORIGINS = False
```

**MIDDLEWARE** - Assurer ordre correct:
```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # ⚡ Must be after SecurityMiddleware
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.middleware.http.ConditionalGetMiddleware',  # ⚡ ETags support
]
```

---

### 6️⃣ Build & Deploy (2 minutes)

```bash
# Stop containers
docker-compose down

# Rebuild with new dependencies
docker-compose build --no-cache

# Start optimized stack
docker-compose up -d

# Check logs
docker-compose logs -f web

# Check Redis
docker-compose exec redis redis-cli ping
# Should return: PONG
```

---

## ✅ VÉRIFICATION

### Test 1: Gunicorn Workers
```bash
docker-compose exec web ps aux | grep gunicorn
# Should show 4-5 gunicorn processes
```

### Test 2: Redis Connection
```bash
docker-compose exec redis redis-cli
> PING
# Should return: PONG
> DBSIZE
# Should show number of cached keys
> exit
```

### Test 3: Cache Functionality
```python
# Dans Django shell
docker-compose exec web python manage.py shell

>>> from django.core.cache import cache
>>> cache.set('test', 'hello')
>>> cache.get('test')
'hello'
>>> exit()
```

### Test 4: Performance Benchmark
```bash
# Install Apache Bench
# Windows: download from https://www.apachelounge.com/download/
# Linux/Mac: sudo apt-get install apache2-utils

# Test avant optimizations
ab -n 100 -c 10 http://localhost:8000/

# Test après optimizations (devrait être 5-10x plus rapide)
ab -n 1000 -c 50 http://localhost:8000/
```

---

## 📊 RÉSULTATS ATTENDUS

**AVANT:**
- Requests per second: ~10-20
- Time per request: 50-100ms
- Concurrent users: 10-20

**APRÈS:**
- Requests per second: ~200-500 ⚡ **10-25x**
- Time per request: 2-5ms ⚡ **20x plus rapide**
- Concurrent users: 1000+ ⚡ **50x**

**Cache hits (sitemap):**
- First request: 500ms
- Cached requests: 5ms ⚡ **100x plus rapide**

---

## 🎯 PROCHAINES ÉTAPES

Une fois ces optimizations en place, tu peux:

1. **Ajouter Nginx** (voir OPTIMIZATIONS.md)
2. **Créer Database indexes** (voir OPTIMIZATIONS.md)
3. **Async views** pour scraping ultra-rapide
4. **CDN** (Cloudflare) pour static files

---

## ❓ TROUBLESHOOTING

### Redis connection refused
```bash
# Check Redis is running
docker-compose ps
docker-compose up -d redis

# Check logs
docker-compose logs redis
```

### Gunicorn errors
```bash
# Check logs
docker-compose logs web

# Test Gunicorn locally
docker-compose exec web gunicorn octascraper.wsgi:application --check-config
```

### Cache not working
```python
# settings.py - Add debug
CACHES['default']['OPTIONS']['IGNORE_EXCEPTIONS'] = False

# Check Redis in Django shell
from django.core.cache import cache
cache.set('test', 123)
print(cache.get('test'))
```

---

**🚀 C'EST PARTI !**

Ces 6 étapes vont transformer ton OctaHub en **machine de guerre ultra-rapide** ! 💪
