import os
import requests
from bs4 import BeautifulSoup
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from urllib.parse import urljoin, urlparse
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

# --- Forms ---
class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email']

# --- Utility Functions ---

def normalize_url(url):
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

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
    images, videos, icons, links = [], [], [], []
    
    # Comprehensive image extensions to pick up EVERYTHING
    IMG_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.avif', '.bmp', '.tiff', '.ico', '.heic', '.heif', '.jp2', '.jxr')

    # 1. Icons
    for link in soup.find_all('link', rel=re.compile(r'icon|apple-touch-icon', re.I)):
        href = link.get('href')
        if href:
            full_icon_url = urljoin(url, href).split('#')[0]
            icons.append(full_icon_url)

    # 2. Main Images (including lazy-loading and srcset)
    for tag in soup.find_all(['img', 'source', 'picture']):
        for attr in ['src', 'data-src', 'srcset', 'data-original', 'data-fallback', 'data-lazy-src', 'data-url']:
            val = tag.get(attr)
            if val:
                # Handle srcset (comma separated list)
                if ',' in val:
                    for part in val.split(','):
                        clean_u = part.strip().split(' ')[0]
                        if clean_u: images.append(urljoin(url, clean_u).split('#')[0])
                else:
                    images.append(urljoin(url, val).split('#')[0])

    # 3. CSS Backgrounds and Content (Styles tags and inline)
    css_targets = []
    for style in soup.find_all(['style', 'link']):
        if style.name == 'style': css_targets.append(style.text)
        elif style.get('rel') == ['stylesheet']:
            # Maybe try fetching external CSS? (can be slow, but user wants everything)
            pass
    for tag in soup.find_all(style=True):
        css_targets.append(tag.get('style'))

    for content in css_targets:
        found_urls = re.findall(r'url\(\s*[\'"]?(.*?)[\'"]?\s*\)', content)
        for u in found_urls:
            u = u.strip('\'" ')
            if not u or u.startswith('data:'): continue
            full_url = urljoin(url, u).split('#')[0]
            # Verify it looks like an image if from CSS url()
            if any(full_url.lower().endswith(ext) for ext in IMG_EXTS) or 'image' in full_url.lower():
                images.append(full_url)

    # 4. Videos and Iframes
    for vid in soup.find_all(['video', 'source']):
        src = vid.get('src') or vid.get('data-src')
        if src: videos.append(urljoin(url, src).split('#')[0])
    
    for iframe in soup.find_all('iframe'):
        src = iframe.get('src')
        if src and ('youtube.com' in src or 'vimeo.com' in src): videos.append(src)

    # 5. Internal Links
    domain = urlparse(url).netloc.replace('www.', '')
    for a in soup.find_all('a', href=True):
        full_url = urljoin(url, a['href']).split('#')[0].rstrip('/')
        if domain in urlparse(full_url).netloc:
            links.append(full_url)
            
    # Clean and filter images (remove tracking dots/tiny spacers)
    final_images = []
    seen_paths = set() # To prevent duplicates even with different query params
    
    for img_url in set(images):
        if img_url.startswith('http'):
            # Normalize URL: remove query params and tiny tracking pixels
            clean_url = img_url.split('?')[0].split('#')[0]
            url_path = clean_url.lower()
            
            if url_path in seen_paths: continue
            if any(x in img_url.lower() for x in ['pixel', 'tracking', 'analytics', 'spacer', 'transparent.gif']): continue
            
            seen_paths.add(url_path)
            final_images.append(img_url)

    return final_images, videos, icons, list(set(links))

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
        'detected_keywords': []
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

def analyze_page_tech(url, session, soup, response):
    techs, html = [], response.text
    if 'wp-content' in html: techs.append('WordPress')
    if 'odoo' in html.lower() or 'website_id' in html: techs.append('Odoo')
    if 'shopify' in html.lower(): techs.append('Shopify')
    if 'next' in html and '__NEXT_DATA__' in html: techs.append('Next.js')
    if 'React' in html or 'react.production' in html: techs.append('React')
    if 'bootstrap' in html.lower(): techs.append('Bootstrap')
    if 'tailwind' in html.lower(): techs.append('Tailwind CSS')
    if 'jquery' in html.lower(): techs.append('jQuery')
    if 'cloudflare' in response.headers.get('Server', ''): techs.append('Cloudflare')
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
        if form.is_valid(): user = form.save(); login(request, user); return redirect('index')
    else: form = UserCreationForm()
    return render(request, 'core/signup.html', {'form': form})

def custom_logout(request): auth_logout(request); return redirect('landing')

@login_required
def index(request): return render(request, 'core/index.html')

def info(request): return render(request, 'core/info.html')

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
    if request.method == 'POST':
        form = UserUpdateForm(request.POST, instance=request.user)
        if form.is_valid(): form.save(); return redirect('profile')
    else: form = UserUpdateForm(instance=request.user)
    return render(request, 'core/profile.html', {'form': form})

@login_required
def scrape(request):
    if request.method == 'POST':
        url_raw = request.POST.get('url')
        if not url_raw: return JsonResponse({'error': 'URL manquant'}, status=400)
        start_url = normalize_url(url_raw)
        session = requests.Session()
        all_imgs, all_vids, all_icons, visited, queue = [], [], [], set(), [start_url]
        deep_scan = request.POST.get('deep_scan') == 'on'
        while queue and len(visited) < (10 if deep_scan else 1):
            url = queue.pop(0)
            if url in visited: continue
            visited.add(url)
            imgs, vids, icons, links = get_media_from_page(url, session)
            all_imgs.extend(imgs); all_vids.extend(vids); all_icons.extend(icons)
            if deep_scan:
                for l in links:
                    if l not in visited and l not in queue: queue.append(l)
        final_imgs_urls = list(dict.fromkeys(all_imgs))
        final_imgs = []
        for u in final_imgs_urls[:400]:
            name = os.path.basename(urlparse(u).path)
            if not name or '.' not in name: name = "image_" + str(uuid.uuid4())[:6]
            final_imgs.append({'url': u, 'name': name})
        final_vids = [{'url': u, 'name': f"Video {i+1}"} for i, u in enumerate(list(dict.fromkeys(all_vids))[:50])]
        final_icons = [{'url': u, 'name': f"Icon {i+1}"} for i, u in enumerate(list(dict.fromkeys(all_icons))[:100])]
        request.session['scraped_media'] = {'images': [m['url'] for m in final_imgs], 'videos': [m['url'] for m in final_vids], 'icons': [m['url'] for m in final_icons]}
        return JsonResponse({'images': final_imgs, 'videos': final_vids, 'icons': final_icons})
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
        url = normalize_url(url_raw)
        session = requests.Session()
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        try:
            try:
                res = session.get(url, timeout=10, headers=headers, verify=False)
                res.raise_for_status()
            except:
                if url.startswith('https://'):
                    url = url.replace('https://', 'http://')
                    res = session.get(url, timeout=10, headers=headers, verify=False)
                else: raise
            
            soup = BeautifulSoup(res.text, 'html.parser')
            forms_data = []
            for i, form in enumerate(soup.find_all('form')):
                inputs = []
                for inp in form.find_all(['input', 'textarea', 'select']):
                    label_text = ''
                    if inp.get('id'):
                        label_tag = soup.find('label', attrs={'for': inp.get('id')})
                        if label_tag: label_text = label_tag.get_text().strip()
                    if not label_text: label_text = inp.get('placeholder') or inp.get('name') or inp.get('aria-label') or 'Sans label'
                    inputs.append({'type': inp.get('type', inp.name), 'name': inp.get('name', 'N/A'), 'label': label_text, 'required': inp.has_attr('required')})
                forms_data.append({'id': form.get('id', f'Form {i+1}'), 'action': form.get('action', '#'), 'method': form.get('method', 'GET').upper(), 'inputs': inputs})
            return JsonResponse({'forms': forms_data, 'count': len(forms_data)})
        except requests.exceptions.ConnectionError:
            return JsonResponse({'error': 'La connexion a été refusée par le site cible. Vérifiez l\'URL ou réessayez plus tard.'}, status=400)
        except Exception as e: return JsonResponse({'error': f'Erreur lors du scan: {str(e)}'}, status=400)
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
def keywords_generate(request):
    if request.method == 'POST':
        activity = request.POST.get('activity', ''); country = request.POST.get('country', ''); region = request.POST.get('region', ''); site_type = request.POST.get('site_type', ''); description = request.POST.get('description', '')
        prompt_text = (activity + " " + description).lower(); extracted = re.findall(r'\w{5,}', prompt_text); main_keywords = [w for w in Counter(extracted).most_common(10)]
        results = {'main': [m[0] for m in main_keywords], 'long_tail': [f"{activity} {region}", f"Meilleur {activity} {country}", f"{site_type} {activity} en ligne", f"Services {activity} {region}", f"Comment choisir {activity}", f"Prix {activity} {country}"], 'strategy': f"Pour un site de type {site_type} en {country} ({region}), privilégiez les mots-clés de proximité liés à l'activité {activity}."}
        return JsonResponse(results)
    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

@login_required
def seo_scrape(request):
    if request.method == 'POST':
        url_raw = request.POST.get('url')
        if not url_raw: return JsonResponse({'error': 'URL manquant'}, status=400)
        start_url = normalize_url(url_raw); deep_scan = request.POST.get('deep_scan') == 'on'; session, results, visited, queue = requests.Session(), [], set(), [start_url]
        while queue and len(visited) < (10 if deep_scan else 1):
            url = queue.pop(0)
            if url in visited: continue
            visited.add(url)
            data = get_seo_data(url, session)
            if data:
                results.append(data)
                if deep_scan:
                    _, _, _, links = get_media_from_page(url, session); queue.extend([l for l in links if l not in visited])
        return JsonResponse({'results': results, 'count': len(results)})
    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

@login_required
def crawler_scrape(request):
    if request.method == 'POST':
        url_raw = request.POST.get('url')
        if not url_raw: return JsonResponse({'error': 'URL manquant'}, status=400)
        start_url = normalize_url(url_raw); session = requests.Session()
        try:
            response = session.get(start_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True); response.raise_for_status()
        except:
            if start_url.startswith('https://'):
                try:
                    start_url = start_url.replace('https://', 'http://')
                    response = session.get(start_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True); response.raise_for_status()
                except Exception as e: return JsonResponse({'error': f'Erreur: {str(e)}'}, status=400)
            else: return JsonResponse({'error': 'Impossible de charger le site'}, status=400)
        soup = BeautifulSoup(response.text, 'html.parser'); techs = analyze_page_tech(start_url, session, soup, response); colors, fonts = extract_colors_and_fonts(start_url, session, soup); _, _, _, internal_links = get_media_from_page(start_url, session); whois = get_rdap_info(urlparse(start_url).netloc); color_counts = Counter(colors).most_common(20); font_counts = Counter(fonts).most_common(12)
        return JsonResponse({'techs': techs, 'colors': [c[0] for c in color_counts], 'fonts': [f[0] for f in font_counts], 'page_count': len(internal_links) + 1, 'url': start_url, 'whois': whois})
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
                    if res.status_code == 200: z.writestr(f"{t}/{i}_{os.path.basename(urlparse(url).path) or 'f'}", res.content)
                except: continue
    buf.seek(0); res = HttpResponse(buf.read(), content_type='application/x-zip-compressed'); res['Content-Disposition'] = 'attachment; filename="octascraper_all.zip"'; return res
