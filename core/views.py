import os
import io
import re
import time
import uuid
import zipfile
from collections import Counter
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render

from .views_public import landing, info, terms, privacy, feedback_index
from .views_account import signup, custom_logout, index, profile, admin_users

# --- Utility Functions ---
from .services.site_analysis import (
    analyze_keyword_metrics,
    analyze_page_tech,
    extract_colors_and_fonts,
    generate_seo_summary,
    get_clean_filename,
    get_google_suggestions,
    get_media_from_page,
    get_rdap_info,
    get_seo_data,
    get_sitemap_urls,
    normalize_url,
)

# --- Views ---

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
def scrape(request):
    """🚀 SIMPLE & RELIABLE SCRAPER - Évite les doublons, récupère les images efficacement"""
    if request.method == 'POST':
        url_raw = request.POST.get('url')
        if not url_raw:
            return JsonResponse({'error': 'URL manquant'}, status=400)
        
        # Normaliser l'URL
        url = url_raw.strip()
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        deep_scan = request.POST.get('deep_scan') == 'on'
        start_time = time.time()
        
        # Session HTTP simple avec désactivation SSL
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        session.verify = False
        
        # Collections avec sets pour éviter les doublons automatiquement
        all_images = set()
        all_videos = set()
        all_icons = set()
        all_logos = set()
        visited_pages = set()
        pages_to_visit = [url]
        pages_scanned = 0
        max_pages = 20 if deep_scan else 10
        
        # Tenter de récupérer le sitemap
        sitemap_urls = get_sitemap_urls(url, session)
        sitemap_found = len(sitemap_urls) > 0
        
        # Parcourir les pages
        while pages_to_visit and pages_scanned < max_pages:
            current_url = pages_to_visit.pop(0)
            
            if current_url in visited_pages:
                continue
                
            visited_pages.add(current_url)
            pages_scanned += 1
            
            try:
                # Fetch avec timeout raisonnable
                response = session.get(current_url, timeout=15, allow_redirects=True)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.content, 'html.parser')
                base_url = response.url
                base_domain = urlparse(base_url).netloc
                
                # === 1. IMAGES ===
                for img in soup.find_all('img'):
                    src = img.get('src') or img.get('data-src') or img.get('data-lazy-src') or img.get('data-original')
                    if src:
                        full_url = urljoin(base_url, src).split('?')[0].split('#')[0]
                        if full_url.startswith('http'):
                            # Classifier images vs logos
                            if any(kw in full_url.lower() for kw in ['logo', 'brand', 'favicon']):
                                all_logos.add(full_url)
                            else:
                                all_images.add(full_url)
                
                # === 2. IMAGES EN ARRIÈRE-PLAN CSS ===
                for el in soup.find_all(style=re.compile(r'background.*?url', re.I)):
                    style = el.get('style', '')
                    urls = re.findall(r'url\(["\']?([^)"\']+)["\']?\)', style)
                    for bg_url in urls:
                        full_url = urljoin(base_url, bg_url).split('?')[0].split('#')[0]
                        if full_url.startswith('http') and any(ext in full_url.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg']):
                            if 'logo' in full_url.lower():
                                all_logos.add(full_url)
                            else:
                                all_images.add(full_url)
                
                # === 3. VIDEOS ===
                for video in soup.find_all('video'):
                    poster = video.get('poster')
                    if poster:
                        full_url = urljoin(base_url, poster).split('?')[0].split('#')[0]
                        if full_url.startswith('http'):
                            all_videos.add(full_url)
                    
                    for source in video.find_all('source'):
                        src = source.get('src')
                        if src:
                            full_url = urljoin(base_url, src).split('?')[0].split('#')[0]
                            if full_url.startswith('http'):
                                all_videos.add(full_url)
                
                # === 4. ICONS (favicons, apple-touch-icon) ===
                for link in soup.find_all('link', rel=re.compile(r'icon|apple-touch-icon', re.I)):
                    href = link.get('href')
                    if href:
                        full_url = urljoin(base_url, href).split('?')[0].split('#')[0]
                        if full_url.startswith('http'):
                            all_icons.add(full_url)
                
                # === 5. SRCSET pour images responsive ===
                for img in soup.find_all('img', srcset=True):
                    srcset = img.get('srcset', '')
                    # srcset format: "url1 1x, url2 2x" ou "url1 100w, url2 200w"
                    urls = re.findall(r'([^\s,]+)\s+(?:\d+[wx]|[\d.]+x)', srcset)
                    for src_url in urls:
                        full_url = urljoin(base_url, src_url).split('?')[0].split('#')[0]
                        if full_url.startswith('http'):
                            if 'logo' in full_url.lower():
                                all_logos.add(full_url)
                            else:
                                all_images.add(full_url)
                
                # === 6. Si deep_scan, collecter les liens de la même origine ===
                if deep_scan and pages_scanned < max_pages:
                    for link in soup.find_all('a', href=True):
                        href = link['href']
                        full_link = urljoin(base_url, href).split('?')[0].split('#')[0]
                        link_domain = urlparse(full_link).netloc
                        
                        # Seulement les liens du même domaine
                        if link_domain == base_domain and full_link not in visited_pages:
                            if full_link not in pages_to_visit and len(pages_to_visit) < 30:
                                pages_to_visit.append(full_link)
                
            except Exception:
                continue
        
        # Convertir sets en listes et limiter
        all_images = list(all_images)[:200]
        all_logos = list(all_logos)[:60]
        all_videos = list(all_videos)[:40]
        all_icons = list(all_icons)[:80]
        
        # Préparer la réponse avec noms de fichiers
        images_list = [{'url': img_url, 'name': get_clean_filename(img_url), 'type': 'image'} for img_url in all_images]
        logos_list = [{'url': logo_url, 'name': get_clean_filename(logo_url), 'type': 'logo'} for logo_url in all_logos]
        videos_list = [{'url': vid_url, 'name': get_clean_filename(vid_url), 'type': 'video'} for vid_url in all_videos]
        icons_list = [{'url': icon_url, 'name': get_clean_filename(icon_url), 'type': 'icon'} for icon_url in all_icons]
        
        # Stocker en session pour export ZIP
        request.session['scraped_media'] = {
            'images': all_images,
            'videos': all_videos,
            'icons': all_icons,
            'logos': all_logos,
        }
        
        elapsed = round(time.time() - start_time, 2)
        
        return JsonResponse({
            'images': images_list,
            'videos': videos_list,
            'icons': icons_list,
            'logos': logos_list,
            'stats': {
                'pages_scanned': pages_scanned,
                'sitemap_found': sitemap_found,
                'sitemap_urls': len(sitemap_urls),
                'total_images': len(images_list),
                'total_videos': len(videos_list),
                'total_logos': len(logos_list),
                'total_icons': len(icons_list),
                'elapsed_time': elapsed,
                'pages_per_second': round(pages_scanned / elapsed, 2) if elapsed > 0 else 0,
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

            # Global deduplication with fingerprints
            seen_bases = {}
            for img_url in all_imgs:
                base_url = img_url.split('?')[0].split('#')[0].lower()
                if base_url not in seen_bases:
                    seen_bases[base_url] = img_url
            
            unique_imgs = list(seen_bases.values())[:200] # Increased limit for audit as well
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
