# Create Azure App Service Deployment Package
# Run this from the incident-rca-foundry directory

Write-Host "🚀 Creating Azure deployment package..." -ForegroundColor Cyan

# Ensure we're in the right directory
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

# Clean up old deploy.zip if it exists
if (Test-Path "deploy.zip") {
    Remove-Item "deploy.zip" -Force
    Write-Host "🗑️  Removed old deploy.zip" -ForegroundColor Yellow
}

# Create the ZIP file with necessary files
$filesToZip = @(
    "src",
    "data",
    "requirements.txt",
    "startup.sh",
    "startup.txt",
    ".deployment",
    "Procfile"
)

Write-Host "📦 Compressing files..." -ForegroundColor Cyan
Compress-Archive -Path $filesToZip -DestinationPath "deploy.zip" -Force

# Get the file size
$fileSize = (Get-Item "deploy.zip").Length / 1MB
$fileSizeMB = [math]::Round($fileSize, 2)

Write-Host ""
Write-Host "✅ Deployment package created successfully!" -ForegroundColor Green
Write-Host "   📦 File: deploy.zip" -ForegroundColor White
Write-Host "   📊 Size: $fileSizeMB MB" -ForegroundColor White
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Set startup command in Azure Portal → App Service → Configuration → General settings:" -ForegroundColor White
Write-Host "   gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000 --timeout 120 src.api.main:app" -ForegroundColor Yellow
Write-Host "2. Deploy via Azure CLI:" -ForegroundColor White
Write-Host "   az webapp deploy --resource-group <RG> --name td-rca-api --src-path deploy.zip --type zip" -ForegroundColor Yellow
Write-Host "   OR" -ForegroundColor Yellow
Write-Host "   Go to: https://td-rca-api.scm.azurewebsites.net/ZipDeployUI" -ForegroundColor White
Write-Host "   Drag and drop deploy.zip" -ForegroundColor White
Write-Host ""
Write-Host "📖 Full instructions: docs\azure-appservice-manual-deployment.md" -ForegroundColor Gray
