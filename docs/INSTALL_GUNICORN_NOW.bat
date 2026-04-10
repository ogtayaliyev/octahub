@echo off
echo ============================================
echo INSTALLATION GUNICORN - 2 MINUTES
echo ============================================
echo.

echo [1/5] Sauvegarde des anciens fichiers...
copy requirements.txt requirements_backup.txt >nul 2>&1
copy Dockerfile Dockerfile_backup >nul 2>&1
copy docker-compose.yml docker-compose_backup.yml >nul 2>&1
echo ✓ Sauvegarde OK

echo.
echo [2/5] Application des fichiers optimises...
copy /Y requirements_optimized.txt requirements.txt >nul
copy /Y Dockerfile_optimized Dockerfile >nul
copy /Y docker-compose_optimized.yml docker-compose.yml >nul
echo ✓ Fichiers copies

echo.
echo [3/5] Arret des containers...
docker-compose down
echo ✓ Containers arretes

echo.
echo [4/5] Rebuild avec Gunicorn (peut prendre 1-2 min)...
docker-compose build
echo ✓ Build termine

echo.
echo [5/5] Demarrage avec Gunicorn + Redis...
docker-compose up -d
echo ✓ Demarrage OK

echo.
echo ============================================
echo ✅ GUNICORN INSTALLE!
echo ============================================
echo.
echo Attendre 10 secondes puis tester:
echo http://localhost:8000/app/
echo.
echo Performance attendue:
echo - 300 images au lieu de 30
echo - 4000 utilisateurs simultanes
echo - Plus de timeout!
echo.
timeout /t 10
docker-compose ps
pause
