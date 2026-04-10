import os
import requests
from bs4 import BeautifulSoup
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from urllib.parse import urljoin, urlparse, unquote
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login, logout as auth_logout
import uuid
import re
import zipfile
import io
from collections import Counter
from django import forms
from django.contrib.auth.models import User
from .models import UserProfile
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.robotparser import RobotFileParser

# --- Forms ---
class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name']
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-input'}),
            'email': forms.EmailInput(attrs={'class': 'form-input'}),
            'first_name': forms.TextInput(attrs={'class': 'form-input'}),
            'last_name': forms.TextInput(attrs={'class': 'form-input'}),
        }

class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['phone', 'address', 'city', 'postal_code', 'country', 'bio', 'company', 'job_title', 'website']
        widgets = {
            'bio': forms.Textarea(attrs={'rows': 4, 'class': 'form-input'}),
            'address': forms.TextInput(attrs={'class': 'form-input'}),
            'city': forms.TextInput(attrs={'class': 'form-input'}),
            'postal_code': forms.TextInput(attrs={'class': 'form-input'}),
            'country': forms.TextInput(attrs={'class': 'form-input'}),
            'phone': forms.TextInput(attrs={'class': 'form-input'}),
            'company': forms.TextInput(attrs={'class': 'form-input'}),
            'job_title': forms.TextInput(attrs={'class': 'form-input'}),
            'website': forms.URLInput(attrs={'class': 'form-input'}),
        }

# --- Utility Functions ---

def normalize_url(url):
    """Normalize page URL for crawling deduplication"""
    url = url.strip().lower()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # Remove www. and trailing slash for better matching
    parsed = urlparse(url)
    netloc = parsed.netloc.replace('www.', '')
    path = parsed.path.rstrip('/')
    return f"{parsed.scheme}://{netloc}{path}"

def normalize_image_url(img_url):
    """Normalize image URL for deduplication - removes query params and naming variations"""
    if not img_url or not img_url.startswith('http'):
        return None
    
    # Remove fragment
    clean = img_url.split('#')[0].strip()
    
    # Parse URL 
    from urllib.parse import urlparse, parse_qs, urlunparse
    parsed = urlparse(clean)
    
    # Normalize hostname (remove www)
    netloc = parsed.netloc.lower().replace('www.', '')
    
    # Normalize path (force lowercase for deduplication)
    path = parsed.path.lower()
    
    # Aggressive: strip common resizing suffixes from filename to find the "ID unique"
    # Examples: image-150x150.jpg -> image.jpg, image_thumb.jpg -> image.jpg
    path = re.sub(r'(-\d+x\d+|_thumb|_small|_medium|_large|_optimized)\.(jpg|jpeg|png|webp|avif|gif)$', r'.\2', path)
    
    # Keep only essential query params
    query = ""
    if parsed.query:
        params = parse_qs(parsed.query)
        # Drop common variation parameters that create duplicates
        drop_params = ['w', 'h', 'width', 'height', 'size', 'quality', 'q', 'resize', 'scale', 
                       'fit', 'crop', 'format', 'fm', 'auto', 'dpr', 'cs', 'bg', 'blur', 'ver', 'v', 'version']
        filtered_params = {k: v for k, v in params.items() if k.lower() not in drop_params}
        
        if filtered_params:
            query = '&'.join(f"{k}={v[0]}" for k, v in sorted(filtered_params.items()))
    
    # Rebuild normalized URL (force https for deduplication purposes)
    return f"https://{netloc}{path.rstrip('/')}" + (f"?{query}" if query else "")

def get_clean_filename(url):
    """Extract and decode filename from URL for display"""
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path)
    
    # Decode URL encoding (%20 -> space, %27 -> apostrophe, etc.)
    decoded = unquote(filename)
    
    # If no filename or just a number/weird string, generate friendly name
    if not decoded or len(decoded) < 3 or '.' not in decoded:
        # Try to get a meaningful name from the path
        path_parts = [p for p in parsed.path.split('/') if p]
        if len(path_parts) > 1:
            decoded = unquote(path_parts[-1])
    
    return decoded if decoded else "file_" + str(uuid.uuid4())[:8]

def get_rdap_info(domain):
    try:
        res = requests.get(f"https://rdap.org/domain/{domain}", timeout=5)
        if res.status_code == 200:
            data = res.json()
            whois = {'registrar': 'Anonyme/Inconnu', 'created': 'N/A', 'expires': 'N/A'}
            for event in data.get('events', []):
                if event.get('eventAction') == 'registration': whois['created'] = event.get('eventDate', 'N/A')
                if event.get('eventAction') == 'expiration': whois['expires'] = event.get('eventDate', 'N/A')
            for entity in data.get('entities', []):
                if 'registrar' in entity.get('roles', []):
                    vcard = entity.get('vcardArray', [None, []])[1]
                    for item in vcard:
                        if item[0] == 'fn': whois['registrar'] = item[3]
            return whois
        elif res.status_code == 403: return {'registrar': 'Privé / Restreint', 'created': 'Protégé', 'expires': 'Protégé'}
    except: pass
    return None

def get_media_from_page(url, session):
    try:
        response = session.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}, allow_redirects=True, verify=False)
        response.raise_for_status()
    except:
        if url.startswith('https://'):
            try:
                url = url.replace('https://', 'http://')
                response = session.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True, verify=False)
                response.raise_for_status()
            except: return [], [], [], []
        else: return [], [], [], []
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Use sets from the start to prevent duplicates
    images = set()
    videos = set()
    icons = set()
    links = set()
    
    # Comprehensive image extensions to pick up EVERYTHING
    IMG_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.avif', '.bmp', '.tiff', '.ico', '.heic', '.heif', '.jp2', '.jxr')

    # 1. Icons
    for link in soup.find_all('link', rel=re.compile(r'icon|apple-touch-icon', re.I)):
        href = link.get('href')
        if href:
            full_icon_url = urljoin(url, href).split('#')[0]
            normalized = normalize_image_url(full_icon_url)
            if normalized:
                icons.add(normalized)

    # 2. Main Images (including lazy-loading and srcset)
    for tag in soup.find_all(['img', 'source', 'picture']):
        for attr in ['src', 'data-src', 'srcset', 'data-original', 'data-fallback', 'data-lazy-src', 'data-url', 
                     'data-image', 'data-background', 'data-bg', 'data-thumb', 'loading', 'data-lazy',
                     'data-desktop', 'data-mobile', 'data-full']:
            val = tag.get(attr)
            if val:
                # Handle srcset (comma separated list)
                if ',' in val:
                    for part in val.split(','):
                        clean_u = part.strip().split(' ')[0]
                        if clean_u:
                            full_url = urljoin(url, clean_u).split('#')[0]
                            normalized = normalize_image_url(full_url)
                            if normalized:
                                images.add(normalized)
                else:
                    full_url = urljoin(url, val).split('#')[0]
                    normalized = normalize_image_url(full_url)
                    if normalized:
                        images.add(normalized)
    
    # 2b. Scan all elements with data-* attributes that might contain image URLs
    # This catches images in divs, sections, spans with custom data attributes
    for tag in soup.find_all(True):  # Find all tags
        for attr_name, attr_value in tag.attrs.items():
            if attr_name.startswith('data-') and isinstance(attr_value, str):
                # Check if this attribute value looks like an image URL
                if any(ext in attr_value.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp', '.avif', '.svg', '.gif']):
                    if not attr_value.startswith('data:'):
                        full_url = urljoin(url, attr_value).split('#')[0]
                        normalized = normalize_image_url(full_url)
                        if normalized:
                            images.add(normalized)

    # 3. CSS Backgrounds and Content (Styles tags and external CSS)
    css_targets = []
    # Internal Styles
    for style in soup.find_all('style'):
        css_targets.append(style.text)
    
    # Inline Styles
    for tag in soup.find_all(style=True):
        css_targets.append(tag.get('style'))
        
    # External Styles (Try fetching .css files)
    for css_link in soup.find_all('link', rel=['stylesheet', 'preload']):
        c_href = css_link.get('href')
        if c_href:
            try:
                css_url = urljoin(url, c_href)
                # Only fetch if it's the same domain to avoid huge overhead
                if urlparse(url).netloc in urlparse(css_url).netloc:
                    css_resp = session.get(css_url, timeout=5, verify=False)
                    if css_resp.ok: css_targets.append(css_resp.text)
            except: pass

    for content in css_targets:
        found_urls = re.findall(r'url\(\s*[\'"]?(.*?)[\'"]?\s*\)', content)
        for u in found_urls:
            u = u.strip('\'" ')
            if not u or u.startswith('data:'): continue
            full_url = urljoin(url, u).split('#')[0]
            if any(full_url.lower().endswith(ext) for ext in IMG_EXTS) or 'image' in full_url.lower():
                normalized = normalize_image_url(full_url)
                if normalized:
                    images.add(normalized)

    # 4. DEEP SCAN: regex on the entire page source for anything that looks like an image path
    # This captures images hidden in JSON, JS variables, or weird attributes
    raw_urls = re.findall(r'[\'"]([^\'"]*?\.(?:jpg|jpeg|png|webp|avif|svg|gif|ico|heic)[^\'"]*?)[\'"]', response.text, re.I)
    for r_u in raw_urls:
        # Basic cleanup and joining
        clean_ru = r_u.strip()
        if clean_ru.startswith(('http', '/', './', '../')):
            full_url = urljoin(url, clean_ru).split('#')[0]
            normalized = normalize_image_url(full_url)
            if normalized:
                images.add(normalized)
    
    # 4b. Additional deep scan: capture URLs in non-quoted contexts (JSON, data attributes)
    # Pattern: /path/to/image.webp or https://domain.com/image.jpg
    additional_urls = re.findall(r'(?:https?://[^\s<>"\']+?|/[^\s<>"\']+?)\.(?:jpg|jpeg|png|webp|avif|svg|gif|ico|heic)\b', response.text, re.I)
    for add_u in additional_urls:
        if not add_u.startswith('data:'):
            full_url = urljoin(url, add_u).split('#')[0]
            normalized = normalize_image_url(full_url)
            if normalized:
                images.add(normalized)

    # 5. Videos and Iframes
    for vid in soup.find_all(['video', 'source']):
        src = vid.get('src') or vid.get('data-src')
        if src:
            full_url = urljoin(url, src).split('#')[0]
            videos.add(full_url)
    
    for iframe in soup.find_all('iframe'):
        src = iframe.get('src')
        if src and ('youtube.com' in src or 'vimeo.com' in src):
            videos.add(src)

    # 6. Internal Links
    domain = urlparse(url).netloc.replace('www.', '')
    for a in soup.find_all('a', href=True):
        full_url = urljoin(url, a['href']).split('#')[0].rstrip('/')
        if domain in urlparse(full_url).netloc:
            links.add(full_url)
            
    # Final filtering: remove tracking pixels and tiny images
    final_images = []
    for img_url in images:
        # Skip obvious tracking/analytics images
        if any(x in img_url.lower() for x in ['pixel', 'tracking', 'analytics', 'spacer', 'transparent.gif', '1x1']):
            continue
        final_images.append(img_url)

    return final_images, list(videos), list(icons), list(links)

def get_seo_data(url, session):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = session.get(url, timeout=10, headers=headers, allow_redirects=True, verify=False)
        response.raise_for_status()
    except:
        try:
            if url.startswith('https://'):
                url = url.replace('https://', 'http://')
                response = session.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True, verify=False)
                response.raise_for_status()
            else: return None
        except: return None
        
    soup = BeautifulSoup(response.text, 'html.parser')
    data = {
        'url': url,
        'title': (soup.title.get_text() if (soup.title and soup.title.get_text()) else 'Pas de titre').strip(),
        'description': '',
        'keywords': [],
        'h1': [h.get_text().strip() for h in soup.find_all('h1') if h.get_text().strip()][:5],
        'h2': [h.get_text().strip() for h in soup.find_all('h2') if h.get_text().strip()][:10],
        'canonical': '',
        'links_internal': 0,
        'links_external': 0,
        'detected_keywords': [],
        'hashtags': []
    }
    
    desc = soup.find('meta', attrs={'name': 'description'}) or \
           soup.find('meta', attrs={'property': 'og:description'}) or \
           soup.find('meta', attrs={'name': 'twitter:description'})
    if desc: data['description'] = desc.get('content', '').strip()
    
    meta_kw = soup.find('meta', attrs={'name': 'keywords'})
    if meta_kw: data['keywords'] = [k.strip() for k in meta_kw.get('content', '').split(',') if k.strip()]
    
    text = soup.get_text().lower()
    words = re.findall(r'\w{4,}', text)
    stop_words = {'dans', 'pour', 'avec', 'votre', 'cette', 'plus', 'tous', 'fait', 'être', 'avoir', 'cela', 'this', 'that', 'with', 'from', 'your', 'vous', 'nous', 'elles', 'plus', 'moins'}
    words = [w for w in words if w not in stop_words and not w.isdigit()]
    data['detected_keywords'] = [w[0] for w in Counter(words).most_common(10)]
    
    # Extract hashtags from content
    hashtags = re.findall(r'#(\w{3,})', response.text)
    if hashtags:
        data['hashtags'] = list(set(hashtags[:20]))
    
    canon = soup.find('link', rel='canonical')
    if canon: data['canonical'] = canon.get('href', '')
    
    domain = urlparse(url).netloc.replace('www.', '')
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.startswith(('tel:', 'mailto:', '#', 'javascript:')): continue
        full_link = urljoin(url, href)
        if domain in urlparse(full_link).netloc:
            data['links_internal'] += 1
        else:
            data['links_external'] += 1
            
    return data

def get_sitemap_urls(base_url, session):
    """Tente de récupérer les URLs depuis le sitemap.xml"""
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
                # Parse XML sitemap
                urls = re.findall(r'<loc>(.*?)</loc>', response.text)
                sitemap_urls.extend(urls[:50])  # Limit to 50 URLs from sitemap
                if sitemap_urls:
                    break
        except:
            continue
    
    return list(set(sitemap_urls))

def analyze_page_tech(url, session, soup, response):
    techs, html = [], response.text.lower()
    headers = {k.lower(): v.lower() for k, v in response.headers.items()}
    
    # CMS Detection
    if 'wp-content' in html or 'wp-includes' in html: techs.append('WordPress')
    if 'odoo' in html or 'website_id' in html: techs.append('Odoo')
    if 'shopify' in html: techs.append('Shopify')
    if 'wix.com' in html or 'wixsite' in html: techs.append('Wix')
    if 'drupal' in html: techs.append('Drupal')
    if 'joomla' in html: techs.append('Joomla')
    if 'prestashop' in html: techs.append('PrestaShop')
    if 'magento' in html: techs.append('Magento')
    if 'squarespace' in html: techs.append('Squarespace')
    if 'webflow' in html: techs.append('Webflow')
    
    # JavaScript Frameworks
    if 'next' in html and '__next_data__' in html: techs.append('Next.js')
    if 'nuxt' in html or '__nuxt' in html: techs.append('Nuxt.js')
    if 'react' in html or 'react.production' in html: techs.append('React')
    if 'vue' in html and ('vue.js' in html or 'vue.min.js' in html): techs.append('Vue.js')
    if 'angular' in html and 'ng-' in html: techs.append('Angular')
    if 'svelte' in html: techs.append('Svelte')
    if 'gatsby' in html: techs.append('Gatsby')
    
    # CSS Frameworks
    if 'bootstrap' in html: techs.append('Bootstrap')
    if 'tailwind' in html: techs.append('Tailwind CSS')
    if 'bulma' in html: techs.append('Bulma')
    if 'foundation' in html: techs.append('Foundation')
    if 'materialize' in html: techs.append('Materialize')
    
    # JavaScript Libraries
    if 'jquery' in html: techs.append('jQuery')
    if 'lodash' in html or 'underscore' in html: techs.append('Lodash/Underscore')
    if 'axios' in html: techs.append('Axios')
    if 'gsap' in html: techs.append('GSAP')
    
    # Hosting/CDN (from headers)
    if 'cloudflare' in headers.get('server', ''): techs.append('Cloudflare')
    if 'nginx' in headers.get('server', ''): techs.append('Nginx')
    if 'apache' in headers.get('server', ''): techs.append('Apache')
    if 'vercel' in headers.get('x-vercel-id', ''): techs.append('Vercel')
    if 'netlify' in html or 'netlify' in headers.get('server', ''): techs.append('Netlify')
    
    # Analytics & Tracking
    if 'google-analytics' in html or 'gtag' in html or 'ga(' in html: techs.append('Google Analytics')
    if 'googletagmanager' in html or 'gtm.js' in html: techs.append('Google Tag Manager')
    if 'facebook.com/tr' in html or 'fbevents.js' in html: techs.append('Facebook Pixel')
    if 'hotjar' in html: techs.append('Hotjar')
    if 'mixpanel' in html: techs.append('Mixpanel')
    
    # Payment & E-commerce
    if 'stripe' in html: techs.append('Stripe')
    if 'paypal' in html: techs.append('PayPal')
    if 'woocommerce' in html: techs.append('WooCommerce')
    
    return list(set(techs))

def extract_colors_and_fonts(url, session, soup):
    colors, fonts = [], []
    def parse_css(content):
        colors.extend(re.findall(r'#([0-9a-fA-F]{3,6})\b', content))
        colors.extend(re.findall(r'rgba?\((\d+,\s*\d+,\s*\d+(?:,\s*[0-9.]+)?)\)', content))
        font_matches = re.findall(r'font-family:\s*(.*?)[;}]', content)
        for f in font_matches:
            for single in f.split(','): fonts.append(single.strip('\'" '))
    for style in soup.find_all('style'): parse_css(style.text)
    for tag in soup.find_all(style=True): parse_css(tag.get('style'))
    domain = urlparse(url).netloc.replace('www.', '')
    for link in soup.find_all('link', rel='stylesheet'):
        css_url = urljoin(url, link.get('href'))
        if domain in urlparse(css_url).netloc:
            try:
                css_res = session.get(css_url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
                if css_res.status_code == 200: parse_css(css_res.text)
            except: continue
    final_colors = []
    for c in colors:
        if isinstance(c, str):
            if re.match(r'^[0-9a-fA-F]{3,6}$', c):
                if len(c) == 3: c = ''.join([x*2 for x in c])
                final_colors.append('#' + c.lower())
            elif c.startswith('rgb') or ',' in c: final_colors.append(c if c.startswith('rgb') else f"rgb({c})")
    return list(set(final_colors)), list(set(fonts))

# --- Views ---

def landing(request):
    if request.user.is_authenticated: return redirect('index')
    return render(request, 'core/landing.html')

def signup(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        accept_terms = request.POST.get('accept_terms')
        
        if not accept_terms:
            form.add_error(None, "Vous devez accepter les conditions d'utilisation pour continuer.")
        elif form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('index')
    else:
        form = UserCreationForm()
    return render(request, 'core/signup.html', {'form': form})

def custom_logout(request): auth_logout(request); return redirect('landing')

@login_required
def index(request): return render(request, 'core/index.html')

def info(request): return render(request, 'core/info.html')

def terms(request): return render(request, 'core/terms.html')

def privacy(request): return render(request, 'core/privacy.html')

@login_required
def seo_index(request): return render(request, 'core/seo.html')

@login_required
def crawler_index(request): return render(request, 'core/crawler.html')

@login_required
def vmap_index(request): return render(request, 'core/vmap.html')

@login_required
def audit_index(request): return render(request, 'core/audit.html')

@login_required
def forms_index(request): return render(request, 'core/forms.html')

@login_required
def keywords_index(request): return render(request, 'core/keywords.html')

@login_required
def profile(request):
    # Ensure user has a profile
    if not hasattr(request.user, 'profile'):
        UserProfile.objects.create(user=request.user)
    
    if request.method == 'POST':
        user_form = UserUpdateForm(request.POST, instance=request.user)
        profile_form = UserProfileForm(request.POST, instance=request.user.profile)
        
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            return redirect('profile')
    else:
        user_form = UserUpdateForm(instance=request.user)
        profile_form = UserProfileForm(instance=request.user.profile)
    
    return render(request, 'core/profile.html', {
        'user_form': user_form,
        'profile_form': profile_form
    })

@login_required
def scrape(request):
    """🚀 ULTRA-PROFESSIONAL SCRAPER - Best-in-class media extraction"""
    if request.method == 'POST':
        url_raw = request.POST.get('url')
        if not url_raw: 
            return JsonResponse({'error': 'URL manquant'}, status=400)
        
        start_url = normalize_url(url_raw)
        deep_scan = request.POST.get('deep_scan') == 'on'
        
        # ═══════════════════════════════════════════════════════════════
        # SMART SCRAPER CONFIGURATION - ÉQUILIBRÉ
        # ═══════════════════════════════════════════════════════════════
        config = {
            'max_pages': 8 if deep_scan else 5,  # ⚡ Limité mais suffisant
            'max_workers': 2,  # ⚡ 2 workers pour un peu de parallélisme
            'timeout': 10,  # ⚡ 10s - assez pour charger une page complète
            'retry_attempts': 2,  # ⚡ 2 essais si échec
            'rate_limit': 0.2,  # Rapide mais pas trop
            'respect_robots': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        # Initialize session with connection pooling
        session = requests.Session()
        session.headers.update({'User-Agent': config['user_agent']})
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=3
        )
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        
        # ═══════════════════════════════════════════════════════════════
        # INTELLIGENT URL DISCOVERY & PRIORITY SCORING
        # ═══════════════════════════════════════════════════════════════
        parsed = urlparse(start_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        domain = parsed.netloc.replace('www.', '')
        
        # Priority URLs with scoring (higher = more important)
        priority_urls = {
            start_url: 100,  # Homepage always highest priority
            # Media-rich pages (90-95 score)
            f"{base}/gallery": 95, f"{base}/galerie": 95, f"{base}/photos": 95,
            f"{base}/portfolio": 95, f"{base}/realisations": 95, f"{base}/projets": 95,
            f"{base}/products": 90, f"{base}/produits": 90, f"{base}/catalogue": 90,
            f"{base}/media": 95, f"{base}/images": 95, f"{base}/videos": 95,
            # Content pages (70-80 score)
            f"{base}/blog": 80, f"{base}/news": 80, f"{base}/actualites": 80,
            f"{base}/articles": 80, f"{base}/ressources": 75, f"{base}/resources": 75,
            # About/Team (60-70 score)
            f"{base}/about": 70, f"{base}/a-propos": 70, f"{base}/qui-sommes-nous": 70,
            f"{base}/team": 70, f"{base}/equipe": 70, f"{base}/notre-equipe": 70,
            # Services (50-60 score)
            f"{base}/services": 60, f"{base}/nos-services": 60, f"{base}/solutions": 60,
            # Contact (40 score - usually less media)
            f"{base}/contact": 40, f"{base}/contact-us": 40, f"{base}/nous-contacter": 40,
            # Case studies & testimonials (75 score)
            f"{base}/case-studies": 75, f"{base}/etudes-de-cas": 75,
            f"{base}/temoignages": 75, f"{base}/testimonials": 75, f"{base}/clients": 75,
        }
        
        # Get sitemap URLs
        sitemap_urls = get_sitemap_urls(base, session)
        
        # Build intelligent queue with scoring
        url_queue = []
        visited = set()
        page_cache = {}  # Cache successful responses
        
        # Add priority URLs
        for url, score in priority_urls.items():
            url_queue.append({'url': url, 'score': score, 'depth': 0})
        
        # Add sitemap URLs with medium priority
        for url in sitemap_urls[:50]:
            if url not in priority_urls:
                # Score based on URL patterns
                score = 50
                if any(kw in url.lower() for kw in ['gallery', 'photo', 'image', 'media', 'portfolio']):
                    score = 85
                elif any(kw in url.lower() for kw in ['blog', 'article', 'news', 'product']):
                    score = 70
                url_queue.append({'url': url, 'score': score, 'depth': 0})
        
        # Sort by score (highest first)
        url_queue.sort(key=lambda x: x['score'], reverse=True)
        
        # Remove duplicates
        seen_urls = set()
        unique_queue = []
        for item in url_queue:
            if item['url'] not in seen_urls:
                seen_urls.add(item['url'])
                unique_queue.append(item)
        url_queue = unique_queue
        
        # ═══════════════════════════════════════════════════════════════
        # SMART SCRAPING WITH RETRY LOGIC & ERROR RECOVERY
        # ═══════════════════════════════════════════════════════════════
        all_images = set()
        all_videos = set()
        all_icons = set()
        failed_urls = []
        
        def scrape_with_retry(url, session, max_retries=3):
            """Smart retry with exponential backoff"""
            for attempt in range(max_retries):
                try:
                    # Check cache first
                    if url in page_cache:
                        return page_cache[url]
                    
                    # Adaptive timeout (longer for first attempt)
                    timeout = config['timeout'] + (attempt * 2)
                    
                    response = session.get(
                        url, 
                        timeout=timeout,
                        allow_redirects=True,
                        verify=False
                    )
                    response.raise_for_status()
                    
                    # Cache successful response
                    page_cache[url] = response
                    return response
                    
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 404:
                        return None  # Don't retry 404s
                    if attempt < max_retries - 1:
                        time.sleep(0.5 * (2 ** attempt))  # Exponential backoff
                    
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                    if attempt < max_retries - 1:
                        time.sleep(1 * (2 ** attempt))
                    
                except Exception:
                    if attempt < max_retries - 1:
                        time.sleep(0.3)
            
            return None  # Failed after all retries
        
        def extract_media_advanced(url, session):
            """Advanced media extraction with all techniques"""
            response = scrape_with_retry(url, session)
            if not response:
                return set(), set(), set(), []
            
            imgs, vids, icons, links = get_media_from_page(url, session)
            
            # ─── BONUS: Mine JavaScript data ───
            try:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Extract from JSON-LD structured data
                for script in soup.find_all('script', type='application/ld+json'):
                    try:
                        data = json.loads(script.string)
                        # Recursively find image URLs in JSON
                        def find_images_in_json(obj):
                            images = []
                            if isinstance(obj, dict):
                                for key, value in obj.items():
                                    if key in ['image', 'logo', 'photo', 'thumbnail', 'url'] and isinstance(value, str):
                                        if value.startswith('http'):
                                            images.append(value)
                                    elif isinstance(value, (dict, list)):
                                        images.extend(find_images_in_json(value))
                            elif isinstance(obj, list):
                                for item in obj:
                                    images.extend(find_images_in_json(item))
                            return images
                        
                        json_imgs = find_images_in_json(data)
                        for img_url in json_imgs:
                            normalized = normalize_image_url(img_url)
                            if normalized:
                                imgs.append(normalized)
                    except:
                        pass
                
                # Extract from window.__INITIAL_STATE__ and similar JS variables
                js_data_patterns = [
                    r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
                    r'window\.__data\s*=\s*({.*?});',
                    r'var\s+imageData\s*=\s*({.*?});',
                    r'const\s+images\s*=\s*(\[.*?\]);'
                ]
                for pattern in js_data_patterns:
                    matches = re.findall(pattern, response.text, re.DOTALL)
                    for match in matches:
                        try:
                            # Try to find URLs in JS data
                            js_urls = re.findall(r'https?://[^\s<>"\']+?\.(?:jpg|jpeg|png|webp|avif|svg|gif)', match)
                            for js_url in js_urls:
                                normalized = normalize_image_url(js_url)
                                if normalized:
                                    imgs.append(normalized)
                        except:
                            pass
                
            except:
                pass
            
            return set(imgs), set(vids), set(icons), links
        
        # ═══════════════════════════════════════════════════════════════
        # PARALLEL SCRAPING WITH THREAD POOL
        # ═══════════════════════════════════════════════════════════════
        pages_processed = 0
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=config['max_workers']) as executor:
            futures = {}
            
            while url_queue and pages_processed < config['max_pages']:
                # Submit batch of URLs
                batch_size = min(config['max_workers'] * 2, len(url_queue))
                batch = url_queue[:batch_size]
                url_queue = url_queue[batch_size:]
                
                for item in batch:
                    url = item['url']
                    if url in visited:
                        continue
                    
                    visited.add(url)
                    future = executor.submit(extract_media_advanced, url, session)
                    futures[future] = {'url': url, 'score': item['score'], 'depth': item['depth']}
                
                # Process completed futures
                for future in as_completed(futures):
                    try:
                        imgs, vids, icons, links = future.result(timeout=config['timeout'] + 5)
                        item_data = futures[future]
                        
                        all_images.update(imgs)
                        all_videos.update(vids)
                        all_icons.update(icons)
                        
                        pages_processed += 1
                        
                        # In deep scan, add discovered links with lower priority
                        if deep_scan and item_data['depth'] < 2 and pages_processed < config['max_pages']:
                            for link in links[:10]:  # Limit discovered links per page
                                if link not in visited and domain in urlparse(link).netloc:
                                    # Score based on URL content
                                    link_score = 30
                                    if any(kw in link.lower() for kw in ['gallery', 'photo', 'image']):
                                        link_score = 60
                                    elif any(kw in link.lower() for kw in ['blog', 'article', 'product']):
                                        link_score = 45
                                    
                                    url_queue.append({
                                        'url': link,
                                        'score': link_score,
                                        'depth': item_data['depth'] + 1
                                    })
                        
                        # Rate limiting
                        time.sleep(config['rate_limit'])
                        
                    except Exception:
                        failed_urls.append(futures[future]['url'])
                    
                    del futures[future]
                
                # Re-sort queue by score
                url_queue.sort(key=lambda x: x['score'], reverse=True)
        
        # ═══════════════════════════════════════════════════════════════
        # INTELLIGENT CLASSIFICATION & DEDUPLICATION
        # ═══════════════════════════════════════════════════════════════
        final_imgs, final_svgs, final_logos, final_videos = [], [], [], []
        
        VIDEO_EXTS = ('.mp4', '.webm', '.ogg', '.mov', '.avi', '.mkv', '.flv', '.wmv')
        SVG_EXT = '.svg'
        IMG_EXTS = ('.jpg', '.jpeg', '.png', '.webp', '.avif', '.gif', '.bmp', '.tiff', '.heic', '.heif')
        
        # Process images with smart classification
        for url in all_images:
            parsed = urlparse(url)
            path = parsed.path.lower()
            name = get_clean_filename(url)
            
            # Classify based on URL patterns and extensions
            if path.endswith(VIDEO_EXTS):
                final_videos.append({'url': url, 'name': name, 'type': 'video'})
            elif path.endswith(SVG_EXT):
                final_svgs.append({'url': url, 'name': name, 'type': 'svg'})
            elif any(kw in path for kw in ['logo', 'brand', 'favicon', 'icon']):
                final_logos.append({'url': url, 'name': name, 'type': 'logo'})
            elif any(path.endswith(ext) for ext in IMG_EXTS):
                final_imgs.append({'url': url, 'name': name, 'type': 'image'})
            else:
                # Unknown type - add as image if it has image-like keywords
                if any(kw in path for kw in ['img', 'image', 'photo', 'pic', 'thumb']):
                    final_imgs.append({'url': url, 'name': name, 'type': 'image'})
        
        # Process videos from video tags
        for url in all_videos:
            name = get_clean_filename(url)
            if name.startswith('file_'):
                name = f"Video_{len(final_videos)+1}"
            final_videos.append({'url': url, 'name': name, 'type': 'video'})
        
        # Process icons
        final_icons_list = []
        for url in list(all_icons)[:100]:
            name = get_clean_filename(url)
            final_icons_list.append({'url': url, 'name': name, 'type': 'icon'})
        
        # Merge SVGs with icons
        final_icons_merged = final_svgs[:40] + final_icons_list
        
        # Limit results - ⚡ Limité pour vitesse mais fonctionnel
        final_imgs = final_imgs[:100]  # ⚡ 100 images (bon compromis)
        final_logos = final_logos[:40]  # ⚡ 40 logos
        final_videos = final_videos[:25]  # ⚡ 25 vidéos
        final_icons_merged = final_icons_merged[:50]  # ⚡ 50 icônes
        
        # Calculate performance metrics
        elapsed_time = round(time.time() - start_time, 2)
        pages_per_second = round(pages_processed / elapsed_time, 2) if elapsed_time > 0 else 0
        
        # Store in session
        request.session['scraped_media'] = {
            'images': [m['url'] for m in final_imgs],
            'videos': [m['url'] for m in final_videos],
            'icons': [m['url'] for m in final_icons_merged],
            'logos': [m['url'] for m in final_logos],
        }
        
        return JsonResponse({
            'images': final_imgs,
            'videos': final_videos,
            'icons': final_icons_merged,
            'logos': final_logos,
            'stats': {
                'pages_scanned': pages_processed,
                'sitemap_found': len(sitemap_urls) > 0,
                'sitemap_urls': len(sitemap_urls),
                'total_images': len(final_imgs),
                'total_videos': len(final_videos),
                'total_logos': len(final_logos),
                'total_icons': len(final_icons_merged),
                'elapsed_time': elapsed_time,
                'pages_per_second': pages_per_second,
                'failed_urls': len(failed_urls),
                'cache_hits': len(page_cache),
                'deep_scan': deep_scan
            }
        })
    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

@login_required
def audit_scrape(request):
    if request.method == 'POST':
        url_raw = request.POST.get('url')
        if not url_raw: return JsonResponse({'error': 'URL manquant'}, status=400)
        start_url = normalize_url(url_raw)
        deep_scan = request.POST.get('deep_scan') == 'on'
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})
        
        all_imgs = []
        visited = set()
        queue = [start_url]
        
        try:
            # Crawling logic
            limit = 10 if deep_scan else 1
            while queue and len(visited) < limit:
                current_url = queue.pop(0)
                if current_url in visited: continue
                visited.add(current_url)
                
                try:
                    imgs, _, _, links = get_media_from_page(current_url, session)
                    all_imgs.extend(imgs)
                    if deep_scan:
                        for l in links:
                            if l not in visited and l not in queue:
                                queue.append(l)
                except: continue

            unique_imgs = list(dict.fromkeys(all_imgs))[:100] # Limit results for speed
            results, total_size = [], 0
            
            for img_url in unique_imgs:
                try:
                    # Optimized head request
                    res = session.head(img_url, timeout=5, allow_redirects=True, verify=False)
                    size = int(res.headers.get('Content-Length', 0))
                    if size == 0:
                        res = session.get(img_url, timeout=5, stream=True, verify=False)
                        size = int(res.headers.get('Content-Length', 0))
                except: size = 0
                
                ext = os.path.splitext(urlparse(img_url).path)[1].lower() or '.jpg'
                is_optimized = ext in ['.webp', '.avif', '.svg']
                results.append({
                    'url': img_url,
                    'size': round(size / 1024, 1),
                    'format': ext.replace('.', '').upper(),
                    'is_optimized': is_optimized,
                    'potential_webp': round(size * 0.4 / 1024, 1) if not is_optimized else 0
                })
                total_size += size
                
            return JsonResponse({
                'results': results,
                'total_size': round(total_size / 1024 / 1024, 2),
                'img_count': len(results),
                'pages_scanned': len(visited)
            })
        except requests.exceptions.ConnectionError:
            return JsonResponse({'error': 'La connexion a été refusée par le site cible. Il est possible que le site bloque les scrapers.'}, status=400)
        except Exception as e: return JsonResponse({'error': f'Erreur lors de l\'audit: {str(e)}'}, status=400)
    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

@login_required
def forms_scrape(request):
    if request.method == 'POST':
        url_raw = request.POST.get('url')
        if not url_raw: return JsonResponse({'error': 'URL manquant'}, status=400)
        start_url = normalize_url(url_raw)
        session = requests.Session()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        
        all_forms_data = []
        visited_urls = set()
        
        # Phase 1: Identify high-probability pages (Deep Discovery)
        try:
            home_res = session.get(start_url, timeout=7, headers=headers, verify=False)
            home_soup = BeautifulSoup(home_res.text, 'html.parser')
            base = start_url.rstrip('/')
            
            # Smart URL seed (prioritize common patterns + found links)
            urls_to_scan = [start_url]
            # Heuristic: look for links mentioning contact/devis/etc.
            for a in home_soup.find_all('a', href=True):
                href = a['href'].lower()
                if any(x in href for x in ['contact', 'devis', 'form', 'inscription', 'signup', 'login', 'contactus']):
                    full_link = urljoin(start_url, a['href']).split('#')[0].rstrip('/')
                    if urlparse(start_url).netloc in urlparse(full_link).netloc:
                        urls_to_scan.append(full_link)
            
            # Static fallbacks just in case
            urls_to_scan.extend([f"{base}/contactus", f"{base}/contact", f"{base}/devis", f"{base}/contact-us"])
            urls_to_scan = list(dict.fromkeys(urls_to_scan))[:8] # Top 8 candidates
        except:
            urls_to_scan = [start_url]

        # Phase 2: Systematic Extraction
        for url in urls_to_scan:
            if url in visited_urls: continue
            visited_urls.add(url)
            try:
                res = session.get(url, timeout=7, headers=headers, verify=False)
                if not res.ok: continue
                soup = BeautifulSoup(res.text, 'html.parser')
                
                # Internal function to extract field data
                def get_field_info(el):
                    tp = el.get('type', el.name).lower()
                    if tp in ['hidden', 'submit', 'button']: return None
                    
                    # Logic 999999999: Label discovery
                    label = ''
                    if el.get('id'):
                        lb = soup.find('label', attrs={'for': el.get('id')})
                        if lb: label = lb.get_text().strip()
                    if not label: label = el.get('placeholder') or el.get('name') or el.get('aria-label') or 'Champ'
                    
                    return {
                        'type': tp,
                        'name': el.get('name', 'N/A'),
                        'id': el.get('id', 'N/A'),
                        'label': label.capitalize(),
                        'required': el.has_attr('required') or 'required' in (el.get('class') or [])
                    }

                # Strategy A: Standard Forms
                for i, form in enumerate(soup.find_all('form')):
                    fields = []
                    for inp in form.find_all(['input', 'textarea', 'select']):
                        info = get_field_info(inp)
                        if info: fields.append(info)
                    
                    if fields:
                        all_forms_data.append({
                            'page': urlparse(url).path or '/',
                            'id': form.get('id', form.get('name', f'Form {len(all_forms_data)+1}')),
                            'action': form.get('action', '#'),
                            'method': form.get('method', 'POST').upper(),
                            'fields': fields,
                            'tech': 'Standard HTML'
                        })

                # Strategy B: Odoo / Dynamic JS Forms (Containers)
                # Look for divs that look like forms
                for container in soup.find_all(['div', 'section'], class_=re.compile(r'form|s_website_form|contact', re.I)):
                    # Check if this container was already handled inside a <form>
                    if container.find_parent('form'): continue
                    
                    fields = []
                    for inp in container.find_all(['input', 'textarea', 'select']):
                        # Only pick if not already categorized
                        info = get_field_info(inp)
                        if info: fields.append(info)
                    
                    if len(fields) >= 2: # High probability of being a real form
                        all_forms_data.append({
                            'page': urlparse(url).path or '/',
                            'id': container.get('id') or container.get('class')[0] if container.get('class') else 'Dynamic Form',
                            'action': 'AJAX / Dynamic',
                            'method': 'POST',
                            'fields': fields,
                            'tech': 'Odoo/JS Component'
                        })
            except: continue

        # Deduplicate forms based on fields signature
        unique_forms = []
        seen_sigs = set()
        for f in all_forms_data:
            sig = "-".join([fields['name'] for fields in f['fields']])
            if sig not in seen_sigs:
                seen_sigs.add(sig)
                unique_forms.append(f)

        return JsonResponse({'forms': unique_forms, 'count': len(unique_forms)})
    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)


@login_required
def vmap_scrape(request):
    if request.method == 'POST':
        url_raw = request.POST.get('url')
        if not url_raw: return JsonResponse({'error': 'URL manquant'}, status=400)
        start_url = normalize_url(url_raw); session, nodes, visited, queue = requests.Session(), [], set(), [(start_url, None)]
        while queue and len(visited) < 25:
            url, p_id = queue.pop(0)
            if url in visited: continue
            visited.add(url)
            n_id = str(uuid.uuid4())[:8]
            nodes.append({'id': n_id, 'url': url, 'parent': p_id, 'path': urlparse(url).path or '/'})
            try:
                _, _, _, links = get_media_from_page(url, session)
                for l in links:
                    if l not in visited: queue.append((l, n_id))
            except: continue
        return JsonResponse({'nodes': nodes})
    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

@login_required
def get_google_suggestions(query, country_code='fr'):
    """Fetch Google Suggest autocomplete suggestions"""
    suggestions = []
    try:
        # Google Suggest API endpoint
        params = {
            'client': 'firefox',
            'q': query,
            'hl': country_code
        }
        response = requests.get('https://suggestqueries.google.com/complete/search', 
                              params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if len(data) > 1 and isinstance(data[1], list):
                suggestions = data[1][:10]  # Top 10 suggestions
    except:
        pass
    return suggestions

def analyze_keyword_metrics(keyword, activity, region):
    """Calculate keyword metrics and categorization"""
    keyword_lower = keyword.lower()
    
    # Intent classification
    intent = 'informational'
    if any(word in keyword_lower for word in ['acheter', 'prix', 'tarif', 'devis', 'commander', 'acheter', 'boutique', 'magasin']):
        intent = 'transactional'
    elif any(word in keyword_lower for word in ['meilleur', 'comparatif', 'avis', 'vs', 'ou', 'choisir']):
        intent = 'commercial'
    elif any(word in keyword_lower for word in ['comment', 'pourquoi', 'quoi', 'qui', 'guide', 'tutoriel']):
        intent = 'informational'
    
    # Difficulty estimation (based on word count and competitiveness)
    word_count = len(keyword.split())
    if word_count >= 4:
        difficulty = 'facile'
        difficulty_score = 25
    elif word_count == 3:
        difficulty = 'moyen'
        difficulty_score = 50
    else:
        difficulty = 'difficile'
        difficulty_score = 75
    
    # Relevance score (0-100)
    relevance = 50  # Base score
    if activity.lower() in keyword_lower:
        relevance += 30
    if region and region.lower() in keyword_lower:
        relevance += 20
    relevance = min(relevance, 100)
    
    # Estimated search volume (simplified categorization)
    if word_count >= 4:
        volume = 'faible'
        volume_estimate = '100-500/mois'
    elif word_count == 3:
        volume = 'moyen'
        volume_estimate = '500-2K/mois'
    else:
        volume = 'élevé'
        volume_estimate = '2K-10K/mois'
    
    return {
        'keyword': keyword,
        'intent': intent,
        'difficulty': difficulty,
        'difficulty_score': difficulty_score,
        'relevance': relevance,
        'volume': volume,
        'volume_estimate': volume_estimate,
        'word_count': word_count
    }

def keywords_generate(request):
    if request.method == 'POST':
        activity = request.POST.get('activity', '').strip()
        country = request.POST.get('country', '').strip()
        region = request.POST.get('region', '').strip()
        site_type = request.POST.get('site_type', '')
        description = request.POST.get('description', '').strip()
        
        if not activity:
            return JsonResponse({'error': 'Activité requise'}, status=400)
        
        # Country code mapping
        country_codes = {
            'france': 'fr', 'belgique': 'be', 'suisse': 'ch', 'canada': 'ca',
            'maroc': 'ma', 'algérie': 'dz', 'tunisie': 'tn'
        }
        country_code = country_codes.get(country.lower(), 'fr')
        
        all_keywords = []
        
        # 1. Base keyword variations
        base_keywords = [
            activity,
            f"{activity} {region}" if region else None,
            f"{activity} {country}",
        ]
        base_keywords = [k for k in base_keywords if k]
        
        # 2. Google Suggest - Main activity
        suggestions_main = get_google_suggestions(activity, country_code)
        all_keywords.extend(suggestions_main)
        
        # 3. Google Suggest - With location
        if region:
            suggestions_local = get_google_suggestions(f"{activity} {region}", country_code)
            all_keywords.extend(suggestions_local)
        
        # 4. Intent-based keywords
        intent_templates = {
            'transactional': [
                f"acheter {activity}",
                f"prix {activity}",
                f"devis {activity}",
                f"tarif {activity} {region}" if region else f"tarif {activity}",
                f"commander {activity} en ligne",
            ],
            'commercial': [
                f"meilleur {activity}",
                f"comparatif {activity}",
                f"avis {activity}",
                f"choisir {activity}",
                f"top {activity} {country}",
            ],
            'informational': [
                f"comment {activity}",
                f"guide {activity}",
                f"qu'est-ce que {activity}",
                f"pourquoi {activity}",
                f"avantages {activity}",
            ]
        }
        
        for intent_type, templates in intent_templates.items():
            all_keywords.extend(templates)
        
        # 5. Question-based keywords (featured snippets opportunities)
        question_keywords = [
            f"quel {activity} choisir",
            f"comment trouver {activity}",
            f"où trouver {activity} {region}" if region else f"où trouver {activity}",
            f"combien coûte {activity}",
            f"quand faire appel à {activity}",
        ]
        all_keywords.extend(question_keywords)
        
        # 6. Long-tail with site type
        if site_type:
            site_keywords = [
                f"{site_type} {activity}",
                f"{activity} professionnel {region}" if region else f"{activity} professionnel",
            ]
            all_keywords.extend([k for k in site_keywords if k])
        
        # Remove duplicates while preserving order
        seen = set()
        unique_keywords = []
        for kw in all_keywords:
            if kw and kw.lower() not in seen:
                seen.add(kw.lower())
                unique_keywords.append(kw)
        
        # Analyze all keywords
        analyzed_keywords = []
        for kw in unique_keywords[:50]:  # Limit to 50 keywords
            metrics = analyze_keyword_metrics(kw, activity, region or '')
            analyzed_keywords.append(metrics)
        
        # Sort by relevance
        analyzed_keywords.sort(key=lambda x: x['relevance'], reverse=True)
        
        # Categorize keywords
        categorized = {
            'high_priority': [],  # High relevance, low difficulty
            'quick_wins': [],     # Medium relevance, easy difficulty
            'long_term': [],      # High volume, high difficulty
            'informational': [],
            'transactional': [],
            'commercial': []
        }
        
        for kw_data in analyzed_keywords:
            # By intent
            categorized[kw_data['intent']].append(kw_data)
            
            # By strategy
            if kw_data['relevance'] >= 70 and kw_data['difficulty'] in ['facile', 'moyen']:
                categorized['high_priority'].append(kw_data)
            elif kw_data['difficulty'] == 'facile':
                categorized['quick_wins'].append(kw_data)
            elif kw_data['difficulty'] == 'difficile' and kw_data['volume'] == 'élevé':
                categorized['long_term'].append(kw_data)
        
        # Generate strategy text
        strategy = f"""
        🎯 <b>Stratégie SEO pour {activity}</b><br><br>
        
        <b>Marché :</b> {country} {f'({region})' if region else ''}<br>
        <b>Type de site :</b> {site_type}<br>
        <b>Mots-clés trouvés :</b> {len(analyzed_keywords)}<br><br>
        
        <b>Recommandations :</b><br>
        • Commencez par les <span style="color: var(--success);">{len(categorized['high_priority'])} mots-clés prioritaires</span> (haute pertinence, faible difficulté)<br>
        • Ciblez les <span style="color: var(--accent);">{len(categorized['quick_wins'])} quick wins</span> pour des résultats rapides<br>
        • Créez du contenu informationnel pour capturer les questions fréquentes<br>
        • Optimisez pour les intentions transactionnelles si vous vendez des produits/services
        """
        
        return JsonResponse({
            'keywords': analyzed_keywords[:30],  # Top 30 keywords
            'categories': {
                'high_priority': categorized['high_priority'][:10],
                'quick_wins': categorized['quick_wins'][:10],
                'informational': categorized['informational'][:10],
                'transactional': categorized['transactional'][:10],
            },
            'stats': {
                'total': len(analyzed_keywords),
                'high_priority': len(categorized['high_priority']),
                'quick_wins': len(categorized['quick_wins']),
                'long_term': len(categorized['long_term']),
            },
            'strategy': strategy
        })
    
    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

@login_required
def seo_scrape(request):
    if request.method == 'POST':
        url_raw = request.POST.get('url')
        if not url_raw: return JsonResponse({'error': 'URL manquant'}, status=400)
        
        start_url = normalize_url(url_raw)
        deep_scan = request.POST.get('deep_scan') == 'on'
        session = requests.Session()
        results = []
        visited = set()
        queue = []
        
        # Construct common page URLs to prioritize
        parsed = urlparse(start_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        
        # Priority pages in French and English
        priority_pages = [
            start_url,
            f"{base}/contact", f"{base}/contact-us", f"{base}/nous-contacter",
            f"{base}/about", f"{base}/about-us", f"{base}/a-propos", f"{base}/qui-sommes-nous",
            f"{base}/services", f"{base}/nos-services", f"{base}/solutions",
            f"{base}/team", f"{base}/equipe", f"{base}/notre-equipe",
            f"{base}/products", f"{base}/produits", f"{base}/nos-produits",
            f"{base}/portfolio", f"{base}/realisations", f"{base}/projets",
            f"{base}/blog", f"{base}/actualites", f"{base}/news",
            f"{base}/faq", f"{base}/aide", f"{base}/support"
        ]
        
        # Try to get URLs from sitemap
        sitemap_urls = get_sitemap_urls(base, session)
        
        # Build initial queue: priority pages + sitemap + homepage
        if sitemap_urls:
            queue.extend(sitemap_urls[:30])  # Add up to 30 URLs from sitemap
        queue.extend(priority_pages)
        queue = list(dict.fromkeys(queue))  # Remove duplicates while preserving order
        
        # Set limits based on mode - ⚡ ÉQUILIBRÉ
        max_pages = 8 if deep_scan else 5
        
        while queue and len(visited) < max_pages:
            url = queue.pop(0)
            if url in visited: 
                continue
            visited.add(url)
            
            data = get_seo_data(url, session)
            if data:
                results.append(data)
                
                # In deep scan, also crawl discovered internal links
                if deep_scan and len(visited) < max_pages:
                    _, _, _, links = get_media_from_page(url, session)
                    # Add new links to queue
                    for link in links:
                        if link not in visited and link not in queue:
                            queue.append(link)
        
        # Generate global summary & insights
        summary = generate_seo_summary(results)
        
        return JsonResponse({
            'results': results, 
            'count': len(results),
            'summary': summary
        })
    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

def generate_seo_summary(results):
    """Génère une synthèse globale de toutes les pages analysées"""
    if not results:
        return {
            'conclusion': 'Aucune page analysée',
            'top_keywords': [],
            'top_hashtags': [],
            'all_meta_keywords': [],
            'top_detected_keywords': [],
            'total_pages': 0,
            'avg_internal_links': 0,
            'avg_external_links': 0,
            'pages_without_description': 0
        }
    
    # Aggregate data from all pages
    all_keywords = []
    all_hashtags = []
    all_meta_keywords = []
    all_detected = []
    total_internal = 0
    total_external = 0
    pages_no_desc = 0
    
    for page in results:
        # Meta keywords
        if page.get('keywords'):
            all_meta_keywords.extend(page['keywords'])
        
        # Detected keywords (with frequency)
        if page.get('detected_keywords'):
            all_detected.extend(page['detected_keywords'])
        
        # Hashtags
        if page.get('hashtags'):
            all_hashtags.extend(page['hashtags'])
        
        # Links
        total_internal += page.get('links_internal', 0)
        total_external += page.get('links_external', 0)
        
        # Pages without description
        if not page.get('description') or len(page.get('description', '')) < 10:
            pages_no_desc += 1
    
    # Count frequencies
    keyword_counts = Counter(all_detected)
    hashtag_counts = Counter(all_hashtags)
    meta_keyword_counts = Counter(all_meta_keywords)
    
    # Generate conclusion
    total_pages = len(results)
    avg_internal = round(total_internal / total_pages, 1) if total_pages > 0 else 0
    avg_external = round(total_external / total_pages, 1) if total_pages > 0 else 0
    
    # Build conclusion text
    conclusion_parts = []
    conclusion_parts.append(f"✅ {total_pages} pages analysées avec succès.")
    
    if pages_no_desc > 0:
        conclusion_parts.append(f"⚠️ {pages_no_desc} page(s) sans description meta (important pour le SEO).")
    else:
        conclusion_parts.append("✅ Toutes les pages ont une description meta.")
    
    if avg_internal < 10:
        conclusion_parts.append(f"⚠️ Maillage interne faible ({avg_internal} liens/page en moyenne). Recommandation: augmenter les liens internes.")
    else:
        conclusion_parts.append(f"✅ Bon maillage interne ({avg_internal} liens/page en moyenne).")
    
    if not all_meta_keywords:
        conclusion_parts.append("⚠️ Aucun mot-clé meta défini. Recommandation: ajouter des mots-clés meta pertinents.")
    
    conclusion = " ".join(conclusion_parts)
    
    return {
        'conclusion': conclusion,
        'top_keywords': [{'word': w, 'count': c} for w, c in keyword_counts.most_common(15)],
        'top_hashtags': [{'tag': t, 'count': c} for t, c in hashtag_counts.most_common(10)],
        'all_meta_keywords': list(set(all_meta_keywords)),
        'top_detected_keywords': [w for w, c in keyword_counts.most_common(20)],
        'total_pages': total_pages,
        'avg_internal_links': avg_internal,
        'avg_external_links': avg_external,
        'pages_without_description': pages_no_desc
    }

@login_required
def crawler_scrape(request):
    if request.method == 'POST':
        url_raw = request.POST.get('url')
        if not url_raw: 
            return JsonResponse({'error': 'URL manquant'}, status=400)
        
        start_url = normalize_url(url_raw)
        session = requests.Session()
        
        # Base URL for crawling
        parsed = urlparse(start_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        
        # Priority pages for technology/design analysis
        priority_pages = [
            start_url,
            f"{base}/about", f"{base}/a-propos",
            f"{base}/services", f"{base}/products", f"{base}/produits",
            f"{base}/contact", f"{base}/blog",
        ]
        
        # Get sitemap URLs
        sitemap_urls = get_sitemap_urls(base, session)
        
        # Build crawl queue (limit to 20 pages for technology analysis)
        queue = []
        if sitemap_urls:
            queue.extend(sitemap_urls[:15])
        queue.extend(priority_pages)
        queue = list(dict.fromkeys(queue))[:20]
        
        # Aggregate data from all pages
        all_techs, all_colors, all_fonts = [], [], []
        visited = set()
        pages_analyzed = 0
        
        for page_url in queue:
            if page_url in visited:
                continue
            visited.add(page_url)
            
            try:
                response = session.get(page_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}, allow_redirects=True, verify=False)
                response.raise_for_status()
            except:
                if page_url.startswith('https://'):
                    try:
                        page_url = page_url.replace('https://', 'http://')
                        response = session.get(page_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True, verify=False)
                        response.raise_for_status()
                    except:
                        continue
                else:
                    continue
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Analyze this page
            techs = analyze_page_tech(page_url, session, soup, response)
            colors, fonts = extract_colors_and_fonts(page_url, session, soup)
            
            all_techs.extend(techs)
            all_colors.extend(colors)
            all_fonts.extend(fonts)
            pages_analyzed += 1
        
        # Deduplicate and get top results
        tech_counts = Counter(all_techs)
        color_counts = Counter(all_colors).most_common(24)
        font_counts = Counter(all_fonts).most_common(15)
        
        # Get WHOIS info
        whois = get_rdap_info(urlparse(start_url).netloc)
        
        # Get internal links count from homepage for page estimation
        try:
            home_response = session.get(start_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'}, verify=False)
            home_soup = BeautifulSoup(home_response.text, 'html.parser')
            _, _, _, internal_links = get_media_from_page(start_url, session)
            estimated_pages = len(internal_links) + 1
        except:
            estimated_pages = pages_analyzed
        
        return JsonResponse({
            'techs': list(tech_counts.keys()),
            'colors': [c[0] for c in color_counts],
            'fonts': [f[0] for f in font_counts],
            'page_count': estimated_pages,
            'pages_analyzed': pages_analyzed,
            'sitemap_found': len(sitemap_urls) > 0,
            'url': start_url,
            'whois': whois
        })
    
    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

@login_required
def download_zip(request):
    media = request.session.get('scraped_media', {})
    if not media: return HttpResponse("Rien à télécharger", status=400)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for t, urls in media.items():
            for i, url in enumerate(urls):
                try:
                    res = requests.get(url, timeout=5)
                    if res.status_code == 200:
                        # Use decoded filename for better readability
                        clean_name = get_clean_filename(url) or f'file_{i}'
                        z.writestr(f"{t}/{i}_{clean_name}", res.content)
                except: continue
    buf.seek(0); res = HttpResponse(buf.read(), content_type='application/x-zip-compressed'); res['Content-Disposition'] = 'attachment; filename="octascraper_all.zip"'; return res
