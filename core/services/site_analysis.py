import os
import re
import uuid
from collections import Counter
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup


def normalize_url(url):
    """Normalize URL while preserving the real host (important for sites that require `www`)."""
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    parsed = urlparse(url)
    scheme = parsed.scheme.lower() or 'https'
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip('/')
    return f"{scheme}://{netloc}{path}"


def normalize_image_url(img_url):
    """Keep media URLs valid while doing light cleanup only."""
    if not img_url or not img_url.startswith('http'):
        return None

    clean = img_url.split('#')[0].strip()
    parsed = urlparse(clean)
    if not parsed.netloc:
        return None

    scheme = parsed.scheme.lower() or 'https'
    netloc = parsed.netloc.lower()
    path = parsed.path
    query = parsed.query

    return f"{scheme}://{netloc}{path}" + (f"?{query}" if query else "")


def get_clean_filename(url):
    """Extract and decode filename from URL for display."""
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path)
    decoded = unquote(filename)

    if not decoded or len(decoded) < 3 or '.' not in decoded:
        path_parts = [p for p in parsed.path.split('/') if p]
        if len(path_parts) > 1:
            decoded = unquote(path_parts[-1])

    return decoded if decoded else 'file_' + str(uuid.uuid4())[:8]


def get_rdap_info(domain):
    try:
        res = requests.get(f"https://rdap.org/domain/{domain}", timeout=5)
        if res.status_code == 200:
            data = res.json()
            whois = {'registrar': 'Anonyme/Inconnu', 'created': 'N/A', 'expires': 'N/A'}
            for event in data.get('events', []):
                if event.get('eventAction') == 'registration':
                    whois['created'] = event.get('eventDate', 'N/A')
                if event.get('eventAction') == 'expiration':
                    whois['expires'] = event.get('eventDate', 'N/A')
            for entity in data.get('entities', []):
                if 'registrar' in entity.get('roles', []):
                    vcard = entity.get('vcardArray', [None, []])[1]
                    for item in vcard:
                        if item[0] == 'fn':
                            whois['registrar'] = item[3]
            return whois
        elif res.status_code == 403:
            return {'registrar': 'Privé / Restreint', 'created': 'Protégé', 'expires': 'Protégé'}
    except Exception:
        pass
    return None


def get_media_from_page(url, session, css_cache=None):
    try:
        response = session.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}, allow_redirects=True, verify=False)
        response.raise_for_status()
    except Exception:
        if url.startswith('https://'):
            try:
                url = url.replace('https://', 'http://')
                response = session.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True, verify=False)
                response.raise_for_status()
            except Exception:
                return [], [], [], []
        else:
            return [], [], [], []

    if css_cache is None:
        css_cache = {}
    soup = BeautifulSoup(response.text, 'html.parser')

    images = set()
    videos = set()
    icons = set()
    links = set()

    img_exts = ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.avif', '.bmp', '.tiff', '.ico', '.heic', '.heif', '.jp2', '.jxr')

    for link in soup.find_all('link', rel=re.compile(r'icon|apple-touch-icon', re.I)):
        href = link.get('href')
        if href:
            full_icon_url = urljoin(url, href).split('#')[0]
            normalized = normalize_image_url(full_icon_url)
            if normalized:
                icons.add(normalized)

    for tag in soup.find_all(['img', 'source', 'picture']):
        for attr in ['src', 'data-src', 'srcset', 'data-original', 'data-fallback', 'data-lazy-src', 'data-url', 'data-image', 'data-background', 'data-bg', 'data-thumb', 'loading', 'data-lazy', 'data-desktop', 'data-mobile', 'data-full']:
            val = tag.get(attr)
            if val:
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

    for tag in soup.find_all(True):
        for attr_name, attr_value in tag.attrs.items():
            if attr_name.startswith('data-') and isinstance(attr_value, str):
                if any(ext in attr_value.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp', '.avif', '.svg', '.gif']):
                    if not attr_value.startswith('data:'):
                        full_url = urljoin(url, attr_value).split('#')[0]
                        normalized = normalize_image_url(full_url)
                        if normalized:
                            images.add(normalized)

    css_targets = []
    for style in soup.find_all('style'):
        css_targets.append(style.text)
    for tag in soup.find_all(style=True):
        css_targets.append(tag.get('style'))
    for css_link in soup.find_all('link', rel=['stylesheet']):
        c_href = css_link.get('href')
        if c_href:
            try:
                css_url = urljoin(url, c_href).split('?')[0].split('#')[0]
                if urlparse(url).netloc in urlparse(css_url).netloc:
                    if css_url in css_cache:
                        css_targets.append(css_cache[css_url])
                    else:
                        css_resp = session.get(css_url, timeout=5, verify=False)
                        if css_resp.ok:
                            css_content = css_resp.text
                            css_cache[css_url] = css_content
                            css_targets.append(css_content)
            except Exception:
                pass

    for content in css_targets:
        found_urls = re.findall(r'url\(\s*[\'"]?(.*?)[\'"]?\s*\)', content)
        for item in found_urls:
            item = item.strip('\'" ')
            if not item or item.startswith('data:'):
                continue
            full_url = urljoin(url, item).split('#')[0]
            if any(full_url.lower().endswith(ext) for ext in img_exts) or 'image' in full_url.lower():
                normalized = normalize_image_url(full_url)
                if normalized:
                    images.add(normalized)

    raw_urls = re.findall(r'[\'"]([^\'"]*?\.(?:jpg|jpeg|png|webp|avif|svg|gif|ico|heic)[^\'"]*?)[\'"]', response.text, re.I)
    for raw in raw_urls:
        clean_raw = raw.strip()
        if clean_raw.startswith(('http', '/', './', '../')):
            full_url = urljoin(url, clean_raw).split('#')[0]
            normalized = normalize_image_url(full_url)
            if normalized:
                images.add(normalized)

    additional_urls = re.findall(r'(?:https?://[^\s<>"\']+?|/[^\s<>"\']+?)\.(?:jpg|jpeg|png|webp|avif|svg|gif|ico|heic)\b', response.text, re.I)
    for add_u in additional_urls:
        if not add_u.startswith('data:'):
            full_url = urljoin(url, add_u).split('#')[0]
            normalized = normalize_image_url(full_url)
            if normalized:
                images.add(normalized)

    for vid in soup.find_all(['video', 'source']):
        src = vid.get('src') or vid.get('data-src')
        if src:
            full_url = urljoin(url, src).split('#')[0]
            videos.add(full_url)

    for iframe in soup.find_all('iframe'):
        src = iframe.get('src')
        if src and ('youtube.com' in src or 'vimeo.com' in src):
            videos.add(src)

    domain = urlparse(url).netloc.replace('www.', '')
    for anchor in soup.find_all('a', href=True):
        full_url = urljoin(url, anchor['href']).split('#')[0].rstrip('/')
        if domain in urlparse(full_url).netloc:
            links.add(full_url)

    seen_bases = {}
    for img_url in images:
        if any(x in img_url.lower() for x in ['pixel', 'tracking', 'analytics', 'spacer', 'transparent.gif', '1x1', 'google-analytics', 'facebook.com']):
            continue
        base_url = img_url.split('?')[0].split('#')[0].lower()
        if base_url not in seen_bases:
            seen_bases[base_url] = img_url
        elif '?' in img_url and '?' not in seen_bases[base_url]:
            seen_bases[base_url] = img_url

    return list(seen_bases.values()), list(videos), list(icons), list(links)


def get_seo_data(url, session):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = session.get(url, timeout=10, headers=headers, allow_redirects=True, verify=False)
        response.raise_for_status()
    except Exception:
        try:
            if url.startswith('https://'):
                url = url.replace('https://', 'http://')
                response = session.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True, verify=False)
                response.raise_for_status()
            else:
                return None
        except Exception:
            return None

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

    desc = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', attrs={'property': 'og:description'}) or soup.find('meta', attrs={'name': 'twitter:description'})
    if desc:
        data['description'] = desc.get('content', '').strip()

    meta_kw = soup.find('meta', attrs={'name': 'keywords'})
    if meta_kw:
        data['keywords'] = [k.strip() for k in meta_kw.get('content', '').split(',') if k.strip()]

    text = soup.get_text().lower()
    words = re.findall(r'\w{4,}', text)
    stop_words = {'dans', 'pour', 'avec', 'votre', 'cette', 'plus', 'tous', 'fait', 'être', 'avoir', 'cela', 'this', 'that', 'with', 'from', 'your', 'vous', 'nous', 'elles', 'moins'}
    words = [w for w in words if w not in stop_words and not w.isdigit()]
    data['detected_keywords'] = [w[0] for w in Counter(words).most_common(10)]

    hashtags = re.findall(r'#(\w{3,})', response.text)
    if hashtags:
        data['hashtags'] = list(set(hashtags[:20]))

    canon = soup.find('link', rel='canonical')
    if canon:
        data['canonical'] = canon.get('href', '')

    domain = urlparse(url).netloc.replace('www.', '')
    for anchor in soup.find_all('a', href=True):
        href = anchor['href']
        if href.startswith(('tel:', 'mailto:', '#', 'javascript:')):
            continue
        full_link = urljoin(url, href)
        if domain in urlparse(full_link).netloc:
            data['links_internal'] += 1
        else:
            data['links_external'] += 1

    return data


def get_sitemap_urls(base_url, session):
    """Try to retrieve sitemap URLs."""
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
        except Exception:
            continue

    return list(set(sitemap_urls))


def analyze_page_tech(url, session, soup, response):
    techs, html = [], response.text.lower()
    headers = {k.lower(): v.lower() for k, v in response.headers.items()}

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

    if 'next' in html and '__next_data__' in html: techs.append('Next.js')
    if 'nuxt' in html or '__nuxt' in html: techs.append('Nuxt.js')
    if 'react' in html or 'react.production' in html: techs.append('React')
    if 'vue' in html and ('vue.js' in html or 'vue.min.js' in html): techs.append('Vue.js')
    if 'angular' in html and 'ng-' in html: techs.append('Angular')
    if 'svelte' in html: techs.append('Svelte')
    if 'gatsby' in html: techs.append('Gatsby')

    if 'bootstrap' in html: techs.append('Bootstrap')
    if 'tailwind' in html: techs.append('Tailwind CSS')
    if 'bulma' in html: techs.append('Bulma')
    if 'foundation' in html: techs.append('Foundation')
    if 'materialize' in html: techs.append('Materialize')

    if 'jquery' in html: techs.append('jQuery')
    if 'lodash' in html or 'underscore' in html: techs.append('Lodash/Underscore')
    if 'axios' in html: techs.append('Axios')
    if 'gsap' in html: techs.append('GSAP')

    if 'cloudflare' in headers.get('server', ''): techs.append('Cloudflare')
    if 'nginx' in headers.get('server', ''): techs.append('Nginx')
    if 'apache' in headers.get('server', ''): techs.append('Apache')
    if 'vercel' in headers.get('x-vercel-id', ''): techs.append('Vercel')
    if 'netlify' in html or 'netlify' in headers.get('server', ''): techs.append('Netlify')

    if 'google-analytics' in html or 'gtag' in html or 'ga(' in html: techs.append('Google Analytics')
    if 'googletagmanager' in html or 'gtm.js' in html: techs.append('Google Tag Manager')
    if 'facebook.com/tr' in html or 'fbevents.js' in html: techs.append('Facebook Pixel')
    if 'hotjar' in html: techs.append('Hotjar')
    if 'mixpanel' in html: techs.append('Mixpanel')

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
        for font in font_matches:
            for single in font.split(','):
                fonts.append(single.strip('\'" '))

    for style in soup.find_all('style'):
        parse_css(style.text)
    for tag in soup.find_all(style=True):
        parse_css(tag.get('style'))
    domain = urlparse(url).netloc.replace('www.', '')
    for link in soup.find_all('link', rel='stylesheet'):
        css_url = urljoin(url, link.get('href'))
        if domain in urlparse(css_url).netloc:
            try:
                css_res = session.get(css_url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
                if css_res.status_code == 200:
                    parse_css(css_res.text)
            except Exception:
                continue

    final_colors = []
    for color in colors:
        if isinstance(color, str):
            if re.match(r'^[0-9a-fA-F]{3,6}$', color):
                if len(color) == 3:
                    color = ''.join([x * 2 for x in color])
                final_colors.append('#' + color.lower())
            elif color.startswith('rgb') or ',' in color:
                final_colors.append(color if color.startswith('rgb') else f'rgb({color})')
    return list(set(final_colors)), list(set(fonts))


def get_google_suggestions(query, country_code='fr'):
    suggestions = []
    try:
        params = {
            'client': 'firefox',
            'q': query,
            'hl': country_code
        }
        response = requests.get('https://suggestqueries.google.com/complete/search', params=params, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if len(data) > 1 and isinstance(data[1], list):
                suggestions = data[1][:10]
    except Exception:
        pass
    return suggestions


def analyze_keyword_metrics(keyword, activity, region):
    keyword_lower = keyword.lower()

    intent = 'informational'
    if any(word in keyword_lower for word in ['acheter', 'prix', 'tarif', 'devis', 'commander', 'boutique', 'magasin']):
        intent = 'transactional'
    elif any(word in keyword_lower for word in ['meilleur', 'comparatif', 'avis', 'vs', 'ou', 'choisir']):
        intent = 'commercial'
    elif any(word in keyword_lower for word in ['comment', 'pourquoi', 'quoi', 'qui', 'guide', 'tutoriel']):
        intent = 'informational'

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

    relevance = 50
    if activity.lower() in keyword_lower:
        relevance += 30
    if region and region.lower() in keyword_lower:
        relevance += 20
    relevance = min(relevance, 100)

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


def generate_seo_summary(results):
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

    all_hashtags = []
    all_meta_keywords = []
    all_detected = []
    total_internal = 0
    total_external = 0
    pages_no_desc = 0

    for page in results:
        if page.get('keywords'):
            all_meta_keywords.extend(page['keywords'])
        if page.get('detected_keywords'):
            all_detected.extend(page['detected_keywords'])
        if page.get('hashtags'):
            all_hashtags.extend(page['hashtags'])
        total_internal += page.get('links_internal', 0)
        total_external += page.get('links_external', 0)
        if not page.get('description') or len(page.get('description', '')) < 10:
            pages_no_desc += 1

    keyword_counts = Counter(all_detected)
    hashtag_counts = Counter(all_hashtags)
    total_pages = len(results)
    avg_internal = round(total_internal / total_pages, 1) if total_pages > 0 else 0
    avg_external = round(total_external / total_pages, 1) if total_pages > 0 else 0

    conclusion_parts = [f"✅ {total_pages} pages analysées avec succès."]
    if pages_no_desc > 0:
        conclusion_parts.append(f"⚠️ {pages_no_desc} page(s) sans description meta (important pour le SEO).")
    else:
        conclusion_parts.append('✅ Toutes les pages ont une description meta.')
    if avg_internal < 10:
        conclusion_parts.append(f"⚠️ Maillage interne faible ({avg_internal} liens/page en moyenne). Recommandation: augmenter les liens internes.")
    else:
        conclusion_parts.append(f"✅ Bon maillage interne ({avg_internal} liens/page en moyenne).")
    if not all_meta_keywords:
        conclusion_parts.append('⚠️ Aucun mot-clé meta défini. Recommandation: ajouter des mots-clés meta pertinents.')

    return {
        'conclusion': ' '.join(conclusion_parts),
        'top_keywords': [{'word': w, 'count': c} for w, c in keyword_counts.most_common(15)],
        'top_hashtags': [{'tag': t, 'count': c} for t, c in hashtag_counts.most_common(10)],
        'all_meta_keywords': list(set(all_meta_keywords)),
        'top_detected_keywords': [w for w, c in keyword_counts.most_common(20)],
        'total_pages': total_pages,
        'avg_internal_links': avg_internal,
        'avg_external_links': avg_external,
        'pages_without_description': pages_no_desc
    }
