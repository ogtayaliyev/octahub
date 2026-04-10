# 🔧 SCRAPER SIMPLE - Version qui MARCHE

# Cette version remplace la fonction scrape() compliquée par une version simple
# Coller ce code dans core/views.py à la ligne 537 (remplacer la fonction scrape existante)

@login_required
def scrape(request):
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
        
        # Session HTTP simple
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        session.verify = False  # Skip SSL verification
        
        # Collections pour stocker les résultats
        all_images = []
        all_videos = []
        all_icons = []
        all_logos = []
        visited_pages = set()
        pages_to_visit = [url]
        pages_scanned = 0
        max_pages = 8 if deep_scan else 3
        
        print(f"🚀 Démarrage scraping: {url} (deep_scan={deep_scan})")
        
        # Parcourir les pages
        while pages_to_visit and pages_scanned < max_pages:
            current_url = pages_to_visit.pop(0)
            
            if current_url in visited_pages:
                continue
                
            visited_pages.add(current_url)
            pages_scanned += 1
            
            print(f"📄 Page {pages_scanned}/{max_pages}: {current_url}")
            
            try:
                # Fetch avec timeout raisonnable
                response = session.get(current_url, timeout=15, allow_redirects=True)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.content, 'html.parser')
                base_url = response.url
                
                # === 1. IMAGES ===
                for img in soup.find_all('img'):
                    src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
                    if src:
                        full_url = urljoin(base_url, src)
                        if full_url.startswith('http') and full_url not in all_images:
                            all_images.append(full_url)
                
                # === 2. LOGOS (images avec 'logo' dans l'URL ou alt) ===
                for img in soup.find_all('img'):
                    src = img.get('src', '')
                    alt = img.get('alt', '')
                    if 'logo' in src.lower() or 'logo' in alt.lower():
                        full_url = urljoin(base_url, src)
                        if full_url.startswith('http') and full_url not in all_logos:
                            all_logos.append(full_url)
                
                # === 3. VIDEOS ===
                for video in soup.find_all('video'):
                    poster = video.get('poster')
                    if poster:
                        full_url = urljoin(base_url, poster)
                        if full_url.startswith('http') and full_url not in all_videos:
                            all_videos.append(full_url)
                
                # === 4. ICONS (favicons, etc.) ===
                for link in soup.find_all('link', rel=lambda x: x and 'icon' in str(x).lower()):
                    href = link.get('href')
                    if href:
                        full_url = urljoin(base_url, href)
                        if full_url.startswith('http') and full_url not in all_icons:
                            all_icons.append(full_url)
                
                # === 5. Si deep_scan, collecter plus de liens ===
                if deep_scan and pages_scanned < max_pages:
                    base_domain = urlparse(base_url).netloc
                    for link in soup.find_all('a', href=True):
                        href = link['href']
                        full_link = urljoin(base_url, href)
                        link_domain = urlparse(full_link).netloc
                        
                        # Seulement les liens du même domaine
                        if link_domain == base_domain and full_link not in visited_pages:
                            if full_link not in pages_to_visit:
                                pages_to_visit.append(full_link)
                
                print(f"✅ Page {pages_scanned}: {len(all_images)} images, {len(all_logos)} logos, {len(all_videos)} vidéos, {len(all_icons)} icônes")
                
            except Exception as e:
                print(f"❌ Erreur sur {current_url}: {str(e)}")
                continue
        
        # Limiter les résultats
        all_images = all_images[:100]
        all_logos = all_logos[:40]
        all_videos = all_videos[:25]
        all_icons = all_icons[:50]
        
        # Préparer la réponse
        images_list = [{'url': url, 'name': url.split('/')[-1], 'type': 'image'} for url in all_images]
        logos_list = [{'url': url, 'name': url.split('/')[-1], 'type': 'logo'} for url in all_logos]
        videos_list = [{'url': url, 'name': url.split('/')[-1], 'type': 'video'} for url in all_videos]
        icons_list = [{'url': url, 'name': url.split('/')[-1], 'type': 'icon'} for url in all_icons]
        
        # Stocker en session
        request.session['scraped_media'] = {
            'images': all_images,
            'videos': all_videos,
            'icons': all_icons,
            'logos': all_logos,
        }
        
        elapsed = round(time.time() - start_time, 2)
        
        print(f"🎉 Scraping terminé en {elapsed}s: {len(all_images)} images, {len(all_logos)} logos")
        
        return JsonResponse({
            'images': images_list,
            'videos': videos_list,
            'icons': icons_list,
            'logos': logos_list,
            'stats': {
                'pages_scanned': pages_scanned,
                'sitemap_found': False,
                'sitemap_urls': 0,
                'total_images': len(all_images),
                'total_videos': len(all_videos),
                'total_logos': len(all_logos),
                'total_icons': len(all_icons),
                'elapsed_time': elapsed,
                'pages_per_second': round(pages_scanned / elapsed, 2) if elapsed > 0 else 0,
                'deep_scan': deep_scan
            }
        })
    
    return JsonResponse({'error': 'Méthode non autorisée'}, status=405)
